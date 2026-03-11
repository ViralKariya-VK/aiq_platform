from django.urls import path
from . import views

urlpatterns = [
    path('', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('teacher/dashboard/', views.teacher_dashboard, name='teacher_dashboard'),
    path('student/dashboard/', views.student_dashboard, name='student_dashboard'),
    path('student/task/<int:task_id>/workspace/', views.workspace, name='workspace'),
    path('student/task/<int:task_id>/prompt/', views.send_prompt, name='send_prompt'),
    path('student/task/<int:task_id>/submit/', views.submit_task, name='submit_task'),
    path('student/task/<int:task_id>/results/', views.results, name='results'),
]