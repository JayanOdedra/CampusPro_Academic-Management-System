from django.contrib import admin
from .models import Profile, Course, Enrollment, Attendance, Marks, LoginAttempt


@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    # Columns shown in the list view
    list_display = ('user', 'role', 'is_approved', 'created_at')
    # Sidebar filters
    list_filter = ('role', 'is_approved')


@admin.register(Course)
class CourseAdmin(admin.ModelAdmin):
    list_display = ('code', 'name', 'teacher', 'created_at')
    list_filter = ('teacher',)
    search_fields = ('code', 'name')


@admin.register(Enrollment)
class EnrollmentAdmin(admin.ModelAdmin):
    list_display = ('student', 'course', 'enrolled_on')
    list_filter = ('course',)


@admin.register(Attendance)
class AttendanceAdmin(admin.ModelAdmin):
    list_display = ('enrollment', 'date', 'present')
    list_filter = ('date', 'present')


@admin.register(Marks)
class MarksAdmin(admin.ModelAdmin):
    list_display = ('enrollment', 'exam_name', 'score', 'max_score')
    list_filter = ('exam_name',)


@admin.register(LoginAttempt)
class LoginAttemptAdmin(admin.ModelAdmin):
    # FIX: LoginAttempt was not visible in Django admin before
    list_display = ('username', 'ip_address', 'failed_count', 'locked_until', 'last_attempt')
    search_fields = ('username',)
