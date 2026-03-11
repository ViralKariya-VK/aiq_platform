from django.db import models
from django.contrib.auth.models import AbstractUser


class User(AbstractUser):
    ROLE_CHOICES = [
        ('teacher', 'Teacher'),
        ('student', 'Student'),
    ]
    role = models.CharField(
        max_length=20,
        choices=ROLE_CHOICES,
        default='student'
    )

    def __str__(self):
        return f"{self.username} ({self.role})"


class Batch(models.Model):
    name = models.CharField(max_length=100)
    teacher = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='batches',
        limit_choices_to={'role': 'teacher'}
    )
    students = models.ManyToManyField(
        User,
        related_name='enrolled_batches',
        limit_choices_to={'role': 'student'},
        blank=True
    )

    def __str__(self):
        return f"{self.name} - {self.teacher.username}"


class LabSession(models.Model):
    DIFFICULTY_CHOICES = [
        (1, 'Easy'),
        (2, 'Medium'),
        (3, 'Hard'),
    ]
    AI_MODE_CHOICES = [
        ('guarded', 'Guarded'),
        ('unguarded', 'Unguarded'),
    ]
    created_by = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='lab_sessions',
        limit_choices_to={'role': 'teacher'}
    )
    batch = models.ForeignKey(
        Batch,
        on_delete=models.CASCADE,
        related_name='lab_sessions'
    )
    topic = models.CharField(max_length=200)
    difficulty = models.IntegerField(choices=DIFFICULTY_CHOICES, default=1)
    ai_mode = models.CharField(
        max_length=20,
        choices=AI_MODE_CHOICES,
        default='unguarded'
    )
    is_active = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.topic} - {self.batch.name}"


class StudentTask(models.Model):
    lab_session = models.ForeignKey(
        LabSession,
        on_delete=models.CASCADE,
        related_name='student_tasks'
    )
    student = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='tasks',
        limit_choices_to={'role': 'student'}
    )
    task_description = models.TextField()
    started_at = models.DateTimeField(null=True, blank=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    final_code = models.TextField(blank=True)
    aiq_p1 = models.FloatField(null=True, blank=True)

    def __str__(self):
        return f"{self.student.username} - {self.lab_session.topic}"


class PromptLog(models.Model):
    student_task = models.ForeignKey(
        StudentTask,
        on_delete=models.CASCADE,
        related_name='prompts'
    )
    prompt_text = models.TextField()
    ai_response = models.TextField()
    ai_code_blocks = models.TextField(blank=True)
    code_before = models.TextField(blank=True)
    code_after = models.TextField(blank=True)
    adoption_ratio = models.FloatField(null=True, blank=True)
    timestamp = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Prompt {self.id} - {self.student_task}"