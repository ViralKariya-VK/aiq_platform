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
    
    # Block access if already submitted
    # if task.submitted_at:
    #     return redirect('results', task_id=task_id)

    if not task.started_at:
        task.started_at = timezone.now()
        task.save()

    prompts = task.prompts.all().order_by('timestamp')

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

    # Close previous prompt snapshot
    last_prompt = task.prompts.order_by('timestamp').last()
    if last_prompt and last_prompt.code_after == '':
        last_prompt.code_after = current_code
        last_prompt.adoption_ratio = compute_adoption_ratio(
            last_prompt.ai_code_blocks,
            last_prompt.code_before,
            last_prompt.code_after
        )
        last_prompt.save()

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

    # if task.submitted_at:
    #     return JsonResponse({'error': 'Already submitted'}, status=400)

    data = json.loads(request.body)
    final_code = data.get('current_code', '')

    # Close last prompt snapshot
    last_prompt = task.prompts.order_by('timestamp').last()
    if last_prompt and last_prompt.code_after == '':
        last_prompt.code_after = final_code
        last_prompt.adoption_ratio = compute_adoption_ratio(
            last_prompt.ai_code_blocks,
            last_prompt.code_before,
            last_prompt.code_after
        )
        last_prompt.save()

    # Compute P1 — average adoption ratio across all prompts
    
    # For weighted
    # all_prompts = task.prompts.exclude(adoption_ratio=None)
    # if all_prompts.exists():
    #     avg_adoption = sum(p.adoption_ratio for p in all_prompts) / all_prompts.count()
    #     aiq_p1 = round((1 - avg_adoption) * 100, 2)
    # else:
    #     aiq_p1 = 100.0

    # Temporary Testing
    # Compute P1 — last prompt only (for testing)
    last_scored = task.prompts.exclude(adoption_ratio=None).order_by('timestamp').last()
    if last_scored:
        aiq_p1 = round((1 - last_scored.adoption_ratio) * 100, 2)
    else:
        aiq_p1 = 100.0

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

    # For weighted
    # all_prompts = task.prompts.exclude(adoption_ratio=None)
    # avg_adoption = 0
    # if all_prompts.exists():
    #     avg_adoption = sum(p.adoption_ratio for p in all_prompts) / all_prompts.count()

    # return render(request, 'results.html', {
    #     'task': task,
    #     'avg_adoption': round(avg_adoption * 100, 2),
    #     'total_prompts': task.prompts.count(),
    #     'prompts': task.prompts.order_by('timestamp'),
    # })

    # Temporary Testing
    last_scored = task.prompts.exclude(adoption_ratio=None).order_by('timestamp').last()
    last_adoption = round(last_scored.adoption_ratio * 100, 2) if last_scored else 0

    return render(request, 'results.html', {
        'task': task,
        'avg_adoption': last_adoption,
        'total_prompts': task.prompts.count(),
        'prompts': task.prompts.order_by('timestamp'),
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
        - Always wrap code in ```python blocks."""

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

5. {mode_instruction}

6. TONE:
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


def compute_adoption_ratio(ai_code, code_before, code_after):
    if not ai_code.strip():
        return 0.0

    # Lines too generic to count as meaningful adoption
    BOILERPLATE_PREFIXES = (
        'print(', 'print (', 'def ', 'return', 'import ',
        'from ', 'if __name__', 'pass', 'else:', 'elif ',
        'try:', 'except', 'class ', '#', 'for ', 'while ',
        'with ', 'raise ', 'break', 'continue'
    )

    def is_meaningful(line):
        s = line.strip()
        if not s:
            return False
        # Too short — less than 8 characters is noise
        if len(s) < 8:
            return False
        # Pure boilerplate
        if s.startswith(BOILERPLATE_PREFIXES):
            return False
        return True

    def normalize(code):
        return set(
            line.strip()
            for line in code.splitlines()
            if is_meaningful(line)
        )

    ai_lines = normalize(ai_code)
    before_lines = normalize(code_before)
    after_lines = normalize(code_after)

    # If AI had no meaningful lines, no adoption possible
    if not ai_lines:
        return 0.0

    new_lines = after_lines - before_lines
    adopted_lines = new_lines & ai_lines

    return round(len(adopted_lines) / len(ai_lines), 2)