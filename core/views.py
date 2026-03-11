from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.utils import timezone
from .models import User, Batch, LabSession, StudentTask, PromptLog
import json
import os
import re
import requests
import difflib
from dotenv import load_dotenv

load_dotenv()

GROQ_API_KEY = os.environ.get('GROQ_API_KEY')
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"


# ── Auth ──────────────────────────────────────────────────────────────────────

def login_view(request):
    if request.user.is_authenticated:
        return redirect_by_role(request.user)
    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')
        user = authenticate(request, username=username, password=password)
        if user is not None:
            login(request, user)
            return redirect_by_role(user)
        else:
            return render(request, 'login.html', {
                'error': 'Invalid username or password'
            })
    return render(request, 'login.html')


def redirect_by_role(user):
    if user.role == 'teacher':
        return redirect('teacher_dashboard')
    return redirect('student_dashboard')


def logout_view(request):
    if request.user.is_authenticated and request.user.role == 'student':
        # Clear all active sessions on logout
        StudentTask.objects.filter(
            student=request.user
        ).update(session_started_at=None)
    logout(request)
    return redirect('login')


# ── Teacher ───────────────────────────────────────────────────────────────────

@login_required
def teacher_dashboard(request):
    if request.user.role != 'teacher':
        return redirect('student_dashboard')
    return render(request, 'teacher_dashboard.html', {
        'user': request.user
    })


# ── Student ───────────────────────────────────────────────────────────────────

@login_required
def student_dashboard(request):
    if request.user.role != 'student':
        return redirect('teacher_dashboard')

    student_batches = request.user.enrolled_batches.all()
    active_sessions = LabSession.objects.filter(
        batch__in=student_batches,
        is_active=True
    )

    student_tasks = []
    for session in active_sessions:
        task = StudentTask.objects.filter(
            lab_session=session,
            student=request.user
        ).first()
        if task:
            student_tasks.append(task)

    return render(request, 'student_dashboard.html', {
        'user': request.user,
        'student_tasks': student_tasks
    })


@login_required
def workspace(request, task_id):
    task = get_object_or_404(StudentTask, id=task_id, student=request.user)

    now = timezone.now()

    # Only start a new session if there isn't one already active
    if not task.session_started_at:
        task.session_started_at = now

    if not task.started_at:
        task.started_at = now

    task.save()

    # Only load prompts from current session
    prompts = task.prompts.filter(
        timestamp__gte=task.session_started_at
    ).order_by('timestamp')

    return render(request, 'workspace.html', {
        'task': task,
        'prompts': prompts,
        'ai_mode': task.lab_session.ai_mode
    })


@csrf_exempt
@login_required
def send_prompt(request, task_id):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    task = get_object_or_404(StudentTask, id=task_id, student=request.user)

    data = json.loads(request.body)
    current_code = data.get('current_code', '')
    prompt_text = data.get('prompt_text', '')

    ai_response = call_groq(
        prompt_text,
        current_code,
        task.task_description,
        task.lab_session.ai_mode
    )

    ai_code_blocks = extract_code_blocks(ai_response)

    PromptLog.objects.create(
        student_task=task,
        prompt_text=prompt_text,
        ai_response=ai_response,
        ai_code_blocks=ai_code_blocks,
        code_before=current_code,
        code_after=''
    )

    return JsonResponse({'response': ai_response})


@csrf_exempt
@login_required
def submit_task(request, task_id):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    task = get_object_or_404(StudentTask, id=task_id, student=request.user)

    data = json.loads(request.body)
    final_code = data.get('current_code', '')

    # Get only prompts from current session
    session_prompts = task.prompts.filter(
        timestamp__gte=task.session_started_at
    ) if task.session_started_at else task.prompts.all()

    # Compute P1 using sequence similarity
    aiq_p1_raw = compute_p1_similarity(final_code, session_prompts)
    aiq_p1 = round((1 - aiq_p1_raw) * 100, 2)

    # Save submission
    task.submitted_at = timezone.now()
    task.final_code = final_code
    task.aiq_p1 = aiq_p1
    task.save()

    return JsonResponse({
        'success': True,
        'redirect': f'/student/task/{task_id}/results/'
    })


@login_required
def results(request, task_id):
    task = get_object_or_404(StudentTask, id=task_id, student=request.user)

    if not task.submitted_at:
        return redirect('workspace', task_id=task_id)

    return render(request, 'results.html', {
        'task': task,
        'avg_adoption': round((1 - ((task.aiq_p1 or 100) / 100)) * 100, 2),
        'total_prompts': task.prompts.filter(
            timestamp__gte=task.session_started_at
        ).count() if task.session_started_at else task.prompts.count(),
        'prompts': task.prompts.filter(
            timestamp__gte=task.session_started_at
        ).order_by('timestamp') if task.session_started_at else task.prompts.order_by('timestamp'),
    })


# ── Helpers ───────────────────────────────────────────────────────────────────

def call_groq(prompt, code_context, task_description, ai_mode):
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }

    if ai_mode == 'guarded':
        mode_instruction = """ASSISTANCE MODE: GUARDED
        - Only provide logic hints, algorithm explanations, and conceptual guidance.
        - NEVER write actual code under any circumstances.
        - If student shares code with an error, explain WHY it is wrong but do not fix it.
        - If asked for direct code, decline and redirect them to think through the logic."""
    else:
        mode_instruction = """ASSISTANCE MODE: UNGUARDED
        - You may provide full code examples with clear explanations.
        - Always explain what the code does and why.
        - Always wrap code in ```python blocks.
        - NEVER write docstrings or triple-quoted strings.
        - Keep code clean and minimal — no inline documentation blocks."""

    messages = [
        {
            "role": "system",
            "content": f"""You are a strict Python and Machine Learning coding assistant
embedded inside an academic learning platform.

STRICT DOMAIN RULES — follow without exception:

1. ALLOWED TOPICS ONLY:
   You may ONLY respond to questions about:
   - Python programming (syntax, built-ins, OOP, debugging)
   - Machine Learning concepts and algorithms
   - Data Science libraries: numpy, pandas, matplotlib, sklearn, scipy
   - The assigned coding task
   - Algorithm logic and data structures relevant to the task

2. EVERYTHING ELSE IS OFF LIMITS:
   If the student asks anything outside the above domain —
   general chat, greetings, opinions, unrelated topics, jokes,
   or anything not about Python/ML/the task — respond with EXACTLY:
   "I can only help with Python and Machine Learning topics.
    Please ask me something related to your coding task."

3. VAGUE OR EMPTY QUESTIONS:
   If the prompt is too vague (e.g. "hi", "help", "what do I do",
   "hello", single words) respond with EXACTLY:
   "Please ask a specific question about your task or a Python/ML
    concept you're struggling with."

4. {mode_instruction}

5. TONE:
   Be concise, educational, and encouraging.
   Format responses clearly using markdown."""
        },
        {
            "role": "user",
            "content": f"My current code:\n```python\n{code_context}\n```\n\n{prompt}"
        }
    ]

    try:
        response = requests.post(GROQ_URL, headers=headers, json={
            "model": "llama-3.3-70b-versatile",
            "messages": messages,
            "max_tokens": 1024,
            "temperature": 0.3
        })
        return response.json()['choices'][0]['message']['content']
    except Exception as e:
        return "Something went wrong connecting to the AI. Please try again."


def extract_code_blocks(ai_response):
    blocks = re.findall(r'```(?:python)?\n(.*?)```', ai_response, re.DOTALL)
    if not blocks:
        return ''
    seen = set()
    unique_lines = []
    for block in blocks:
        for line in block.strip().splitlines():
            stripped = line.strip()
            if stripped and stripped not in seen:
                seen.add(stripped)
                unique_lines.append(line)
    return '\n'.join(unique_lines)


def compute_p1_similarity(final_code, session_prompts):
    """
    P1: Sequence similarity between final submitted code
    and each AI code block given during the session.

    Returns the MAXIMUM similarity ratio across all prompts —
    catching the case where student copies from any one response.

    0.0 = completely original
    1.0 = identical to AI response
    """
    if not final_code.strip():
        return 0.0

    # Normalize code for comparison — strip comments and blank lines
    def normalize(code):
        lines = []
        for line in code.splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith('#'):
                lines.append(stripped)
        return '\n'.join(lines)

    normalized_final = normalize(final_code)

    if not normalized_final:
        return 0.0

    max_similarity = 0.0

    for prompt in session_prompts:
        if not prompt.ai_code_blocks or not prompt.ai_code_blocks.strip():
            continue

        normalized_ai = normalize(prompt.ai_code_blocks)

        if not normalized_ai:
            continue

        similarity = difflib.SequenceMatcher(
            None,
            normalized_final,
            normalized_ai
        ).ratio()

        print(f"Prompt {prompt.id} similarity: {similarity:.3f}")

        if similarity > max_similarity:
            max_similarity = similarity

    print(f"Max similarity (P1 raw): {max_similarity:.3f}")
    print(f"AIQ P1: {round((1 - max_similarity) * 100, 2)}")

    # Apply threshold — below 0.5 is structural coincidence, not copying
    COPYING_THRESHOLD = 0.5
    if max_similarity < COPYING_THRESHOLD:
        print(f"Below threshold — treating as original code")
        return 0.0

    return max_similarity
