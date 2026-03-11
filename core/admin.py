from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import User, Batch, LabSession, StudentTask, PromptLog


@admin.register(User)
class CustomUserAdmin(UserAdmin):
    list_display = ['username', 'role', 'email']
    fieldsets = UserAdmin.fieldsets + (
        ('Role', {'fields': ('role',)}),
    )


@admin.register(Batch)
class BatchAdmin(admin.ModelAdmin):
    list_display = ['name', 'teacher']
    filter_horizontal = ['students']


@admin.register(LabSession)
class LabSessionAdmin(admin.ModelAdmin):
    list_display = ['topic', 'batch', 'difficulty', 'ai_mode', 'is_active']


@admin.register(StudentTask)
class StudentTaskAdmin(admin.ModelAdmin):
    list_display = ['student', 'lab_session', 'started_at']


@admin.register(PromptLog)
class PromptLogAdmin(admin.ModelAdmin):
    list_display = ['student_task', 'timestamp']