from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.utils import timezone
from .models import User, Batch, LabSession, StudentTask, PromptLog
import json
import os
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
    task = get_object_or_404(
        StudentTask,
        id=task_id,
        student=request.user
    )
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

    task = get_object_or_404(
        StudentTask,
        id=task_id,
        student=request.user
    )

    data = json.loads(request.body)
    current_code = data.get('current_code', '')
    prompt_text = data.get('prompt_text', '')

    ai_response = call_groq(
        prompt_text,
        current_code,
        task.task_description,
        task.lab_session.ai_mode
    )

    PromptLog.objects.create(
        student_task=task,
        prompt_text=prompt_text,
        ai_response=ai_response,
        code_before=current_code,
    )

    return JsonResponse({'response': ai_response})


# ── Groq ──────────────────────────────────────────────────────────────────────

def call_groq(prompt, code_context, task_description, ai_mode):
    return "I am working."