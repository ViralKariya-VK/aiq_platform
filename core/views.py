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

REJECTED_PREFIXES = (
    "I can only help with Python and Machine Learning topics",
    "Please ask a specific question about your task",
)

LATENCY_BASELINES = {1: 5, 2: 10, 3: 15}
FREQUENCY_BASELINES = {1: 3, 2: 6, 3: 10}


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

    if not task.session_started_at:
        task.session_started_at = now

    if not task.started_at:
        task.started_at = now

    task.save()

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

    is_rejected = any(
        ai_response.strip().startswith(prefix)
        for prefix in REJECTED_PREFIXES
    )

    if is_rejected:
        prompt_quality_score = None
    else:
        prompt_quality_score = score_prompt_quality(
            prompt_text,
            current_code,
            task.task_description
        )

    PromptLog.objects.create(
        student_task=task,
        prompt_text=prompt_text,
        ai_response=ai_response,
        ai_code_blocks=ai_code_blocks,
        code_before=current_code,
        code_after='',
        prompt_quality_score=prompt_quality_score
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

    difficulty = task.lab_session.difficulty

    session_prompts = task.prompts.filter(
        timestamp__gte=task.session_started_at
    ) if task.session_started_at else task.prompts.all()

    total_prompts = session_prompts.count()

    # ── P1 + P2 ───────────────────────────────────────────────────────────────
    COPYING_THRESHOLD = 0.5
    p1_raw, best_prompt = compute_p1_similarity(final_code, session_prompts)

    if p1_raw < COPYING_THRESHOLD:
        aiq_p1 = 100.0
        aiq_p2 = 100.0
    else:
        relevance = score_code_relevance(
            best_prompt.ai_code_blocks,
            task.task_description
        )
        base_p1 = (1 - p1_raw)
        adjusted_p1 = base_p1 + (1 - base_p1) * (1 - relevance)
        aiq_p1 = round(min(adjusted_p1 * 100, 100.0), 2)

        p2_raw = compute_p2_edit_ratio(final_code, best_prompt.ai_code_blocks)
        aiq_p2 = round(p2_raw * 100, 2)

    # ── P3 — Effort + Latency ─────────────────────────────────────────────────
    if total_prompts == 0:
        # No AI used — evaluate final code as effort
        effort_raw = score_independent_effort(
            final_code,
            task.task_description
        )
        aiq_p3 = round(effort_raw * 100, 2)
    else:
        first_prompt = session_prompts.order_by('timestamp').first()
        code_before_first = first_prompt.code_before if first_prompt else ''

        # Effort score — what did student write before asking AI?
        effort_raw = score_independent_effort(
            code_before_first,
            task.task_description
        )

        # Time bonus — how long did they wait?
        if first_prompt and task.session_started_at:
            delta = first_prompt.timestamp - task.session_started_at
            latency_minutes = delta.total_seconds() / 60
            expected_minutes = LATENCY_BASELINES.get(difficulty, 5)
            time_bonus = min(latency_minutes / expected_minutes, 1.0)
        else:
            time_bonus = 0.0

        # Effort 70%, time 30%
        aiq_p3 = round((effort_raw * 0.7 + time_bonus * 0.3) * 100, 2)

    # ── P4 — Prompt Frequency Rate ────────────────────────────────────────────
    if total_prompts == 0:
        aiq_p4 = 100.0
    else:
        expected_prompts = FREQUENCY_BASELINES.get(difficulty, 3)
        aiq_p4 = round(
            max(0.0, (1 - total_prompts / expected_prompts) * 100), 2
        )

    # ── P5 — Prompt Quality Score ─────────────────────────────────────────────
    scored_prompts = session_prompts.exclude(prompt_quality_score=None)
    scores = list(scored_prompts.values_list('prompt_quality_score', flat=True))

    if scores:
        avg_quality = sum(scores) / len(scores)
        aiq_p5 = round((avg_quality / 5.0) * 100, 2)
    else:
        aiq_p5 = None

    # ── AIQ Score ─────────────────────────────────────────────────────────────
    live_scores = [aiq_p1, aiq_p2, aiq_p3, aiq_p4]
    if aiq_p5 is not None:
        live_scores.append(aiq_p5)

    aiq_score = round(sum(live_scores) / len(live_scores), 2)

    # ── Save ──────────────────────────────────────────────────────────────────
    task.submitted_at = timezone.now()
    task.final_code = final_code
    task.aiq_p1 = aiq_p1
    task.aiq_p2 = aiq_p2
    task.aiq_p3 = aiq_p3
    task.aiq_p4 = aiq_p4
    task.aiq_p5 = aiq_p5
    task.aiq_score = aiq_score
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

    session_prompts = task.prompts.filter(
        timestamp__gte=task.session_started_at
    ).order_by('timestamp') if task.session_started_at else task.prompts.order_by('timestamp')

    return render(request, 'results.html', {
        'task': task,
        'total_prompts': session_prompts.count(),
        'prompts': session_prompts,
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
        - Keep code clean and minimal — no inline documentation blocks.
        - NEVER volunteer the complete task solution unless explicitly asked.
        - If student asks a conceptual question, answer only that concept.
        - Do NOT add 'here is how to apply it to your task' sections."""

    messages = [
        {
            "role": "system",
            "content": f"""You are a strict Python and Machine Learning coding assistant
embedded inside an academic learning platform.

The student is working on this task (for your context only — do NOT solve it unless explicitly asked):
{task_description}

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

5. RESPONSE SCOPE — CRITICAL:
   You MUST only answer exactly what was asked. Nothing more.
   - Conceptual question → explain concept only, no full code
   - Specific function question → explain that function only
   - Debug request → fix only that bug
   - NEVER provide complete task solution unprompted
   - NEVER connect your answer back to the full task
   - Treat each question in isolation

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
    except Exception:
        return "Something went wrong connecting to the AI. Please try again."


def score_independent_effort(code, task_description):
    """
    Evaluate how much genuine independent coding effort
    the student made before asking AI.
    Returns 0.0 to 1.0.
    """
    if not code or not code.strip():
        return 0.0

    # Quick check — if only comments or default placeholder
    meaningful_lines = [
        l.strip() for l in code.splitlines()
        if l.strip() and not l.strip().startswith('#')
    ]
    if not meaningful_lines:
        return 0.0

    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }

    messages = [
        {
            "role": "system",
            "content": """You are an academic evaluator assessing how much genuine
independent coding effort a student made before asking an AI for help.

Score from 0.0 to 1.0 using this rubric:

0.0 — Empty, only whitespace, or only comments
0.2 — Only import statements, nothing else
0.3 — Imports and print statements only, no logic
0.5 — Some logic but completely unrelated to the task
0.7 — Partial logic relevant to the task, even if incomplete or incorrect
0.9 — Substantial attempt covering most of the task, even if imperfect
1.0 — Complete or near-complete independent solution

Key principle: Award effort and intent, not correctness.
A wrong but genuine attempt at the task scores higher than correct but unrelated code.

Respond with ONLY a single decimal number between 0.0 and 1.0. Nothing else."""
        },
        {
            "role": "user",
            "content": f"""Assigned task:
{task_description}

Student's code written before asking AI:
{code}

Effort score (0.0-1.0):"""
        }
    ]

    try:
        response = requests.post(GROQ_URL, headers=headers, json={
            "model": "llama-3.3-70b-versatile",
            "messages": messages,
            "max_tokens": 5,
            "temperature": 0.1
        })
        raw = response.json()['choices'][0]['message']['content'].strip()
        score = float(raw)
        return round(min(max(score, 0.0), 1.0), 2)
    except Exception:
        return 0.0


def score_prompt_quality(prompt_text, code_context, task_description):
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }

    messages = [
        {
            "role": "system",
            "content": """You are an academic evaluator assessing the quality of a
student's question to an AI coding assistant.

Score the prompt from 1 to 5 based on these three dimensions:

SPECIFICITY (40% weight):
- 1: Vague or single word ("help", "fix this", "what do I do")
- 5: Precise, mentions specific function, error, or concept

TASK RELEVANCE (40% weight):
- 1: Completely unrelated to the assigned task
- 5: Directly about the task or a concept needed to complete it

OWN THINKING (20% weight):
- 1: No evidence of attempt ("just give me the code")
- 5: Shows reasoning, describes what they tried, or asks why

Compute a weighted score across all three dimensions.
Respond with ONLY a single number between 1 and 5 with one decimal place.
Nothing else. No explanation."""
        },
        {
            "role": "user",
            "content": f"""Assigned task:
{task_description}

Student's current code:
{code_context}

Student's prompt:
{prompt_text}

Score (1.0-5.0):"""
        }
    ]

    try:
        response = requests.post(GROQ_URL, headers=headers, json={
            "model": "llama-3.3-70b-versatile",
            "messages": messages,
            "max_tokens": 5,
            "temperature": 0.1
        })
        raw = response.json()['choices'][0]['message']['content'].strip()
        score = float(raw)
        if 1.0 <= score <= 5.0:
            return score
        return None
    except Exception:
        return None


def score_code_relevance(ai_code, task_description):
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }

    messages = [
        {
            "role": "system",
            "content": """You are an academic evaluator assessing whether a block of
AI-generated code directly solves a student's assigned programming task.

Score from 0.0 to 1.0:
- 1.0: The code directly implements the core solution for the task
- 0.7: The code solves a major component of the task
- 0.5: The code is related to the task but only a peripheral part
- 0.2: The code is a general concept loosely connected to the task
- 0.0: The code is unrelated to the task entirely

Respond with ONLY a single decimal number between 0.0 and 1.0.
Nothing else."""
        },
        {
            "role": "user",
            "content": f"""Assigned task:
{task_description}

AI-generated code shown to student:
{ai_code}

Relevance score (0.0-1.0):"""
        }
    ]

    try:
        response = requests.post(GROQ_URL, headers=headers, json={
            "model": "llama-3.3-70b-versatile",
            "messages": messages,
            "max_tokens": 5,
            "temperature": 0.1
        })
        raw = response.json()['choices'][0]['message']['content'].strip()
        score = float(raw)
        return round(min(max(score, 0.0), 1.0), 2)
    except Exception:
        return 0.5


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


def normalize_code(code):
    lines = []
    for line in code.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith('#'):
            lines.append(stripped)
    return '\n'.join(lines)


def compute_p1_similarity(final_code, session_prompts):
    if not final_code.strip():
        return 0.0, None

    normalized_final = normalize_code(final_code)
    if not normalized_final:
        return 0.0, None

    max_similarity = 0.0
    best_prompt = None

    for prompt in session_prompts:
        if not prompt.ai_code_blocks or not prompt.ai_code_blocks.strip():
            continue

        normalized_ai = normalize_code(prompt.ai_code_blocks)
        if not normalized_ai:
            continue

        similarity = difflib.SequenceMatcher(
            None,
            normalized_final,
            normalized_ai
        ).ratio()

        if similarity > max_similarity:
            max_similarity = similarity
            best_prompt = prompt

    return max_similarity, best_prompt


def compute_p2_edit_ratio(final_code, ai_code):
    if not ai_code or not ai_code.strip():
        return 1.0

    ai_lines = [
        l.strip() for l in normalize_code(ai_code).splitlines() if l.strip()
    ]
    final_lines = [
        l.strip() for l in normalize_code(final_code).splitlines() if l.strip()
    ]

    if not ai_lines:
        return 1.0

    matcher = difflib.SequenceMatcher(None, ai_lines, final_lines)
    matched_lines = sum(
        block.size
        for block in matcher.get_matching_blocks()
        if block.size > 0
    )

    edit_ratio = 1.0 - (matched_lines / len(ai_lines))
    return round(max(0.0, min(1.0, edit_ratio)), 2)