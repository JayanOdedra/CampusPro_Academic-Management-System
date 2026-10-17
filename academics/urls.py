from django.urls import path
from django.contrib.auth import views as auth_views
from django.views.generic import RedirectView
from . import views

urlpatterns = [
     path('', RedirectView.as_view(pattern_name='login', permanent=False), name='home'),
    # Auth
    path('register/', views.register_view, name='register'),
    path('login/',    views.login_view,    name='login'),
    path('logout/',   views.logout_view,   name='logout'),

    # Dashboard router (decides where to send each role)
    path('dashboard/', views.dashboard, name='dashboard'),

    # Role-specific dashboards
    path('teacher/', views.teacher_dashboard, name='teacher_dashboard'),
    path('student/', views.student_dashboard, name='student_dashboard'),

    # Superuser admin panel — Phase 1
    path('admin-panel/',         views.admin_dashboard, name='admin_dashboard'),
    path('admin-panel/pending/', views.pending_users,   name='pending_users'),

    # -----------------------------------------------------------------------
    # Phase 2 — Admin: Course & Enrollment management
    # -----------------------------------------------------------------------
    path('admin-panel/courses/',                    views.course_list,       name='course_list'),
    path('admin-panel/courses/add/',                views.course_add,        name='course_add'),
    path('admin-panel/courses/<int:course_id>/edit/',   views.course_edit,   name='course_edit'),
    path('admin-panel/courses/<int:course_id>/delete/', views.course_delete, name='course_delete'),
    path('admin-panel/courses/<int:course_id>/enroll/', views.enrollment_manage, name='enrollment_manage'),

    # -----------------------------------------------------------------------
    # Phase 2 — Teacher: Attendance & Marks
    # -----------------------------------------------------------------------
    path('teacher/course/<int:course_id>/attendance/', views.teacher_attendance, name='teacher_attendance'),
    path('teacher/course/<int:course_id>/marks/',      views.teacher_marks,      name='teacher_marks'),

    # -----------------------------------------------------------------------
    # Phase 3 — Admin: Users list with search/filter/pagination
    # -----------------------------------------------------------------------
    path('admin-panel/users/', views.users_list, name='users_list'),

    # -----------------------------------------------------------------------
    # Phase 3 — Teacher: Students list per course
    # -----------------------------------------------------------------------
    path('teacher/course/<int:course_id>/students/', views.teacher_course_students, name='teacher_course_students'),

    # -----------------------------------------------------------------------
    # Phase 3 — Student: Full history with filter + pagination
    # -----------------------------------------------------------------------
    path('student/history/', views.student_history, name='student_history'),

    # -----------------------------------------------------------------------
    # FIX (main bug): these two routes were missing. student_dashboard.html uses
    # {% url 'student_attendance' %} and {% url 'student_marks' %}; without them
    # Django raised NoReverseMatch and the student dashboard crashed (HTTP 500).
    # -----------------------------------------------------------------------
    path('student/course/<int:course_id>/attendance/', views.student_attendance, name='student_attendance'),
    path('student/course/<int:course_id>/marks/',      views.student_marks,      name='student_marks'),

    # -----------------------------------------------------------------------
    # Phase 4 — Admin: Audit Log
    # -----------------------------------------------------------------------
    path('admin-panel/audit-log/',     views.audit_log,     name='audit_log'),
    path('admin-panel/audit-log/csv/', views.audit_log_csv, name='audit_log_csv'),

    # -----------------------------------------------------------------------
    # Phase 4 — Notices (all roles)
    # -----------------------------------------------------------------------
    path('notices/',                          views.notices_page,   name='notices_page'),
    path('notices/post/',                     views.notice_post,    name='notice_post'),
    path('notices/<str:notice_id>/delete/',   views.notice_delete,  name='notice_delete'),

    # -----------------------------------------------------------------------
    # Phase 5 — report card, exports, locked accounts
    # -----------------------------------------------------------------------
    path('student/report-card/', views.report_card, name='report_card'),
    path('teacher/course/<int:course_id>/attendance/export/', views.teacher_attendance_csv, name='teacher_attendance_csv'),
    path('admin-panel/users/export/',   views.users_csv,   name='users_csv'),
    path('admin-panel/courses/export/', views.courses_csv, name='courses_csv'),
    path('admin-panel/locked/',                       views.locked_accounts, name='locked_accounts'),
    path('admin-panel/locked/<int:attempt_id>/unlock/', views.unlock_account, name='unlock_account'),

    # -----------------------------------------------------------------------
    # Phase 5 — password reset (Django built-in views, our Bootstrap templates).
    # In development the reset e-mail is printed in the terminal (console backend).
    # -----------------------------------------------------------------------
    path('password-reset/',
         auth_views.PasswordResetView.as_view(
             template_name='password_reset_form.html',
             email_template_name='password_reset_email.txt',
             subject_template_name='password_reset_subject.txt'),
         name='password_reset'),
    path('password-reset/done/',
         auth_views.PasswordResetDoneView.as_view(template_name='password_reset_done.html'),
         name='password_reset_done'),
    path('reset/<uidb64>/<token>/',
         auth_views.PasswordResetConfirmView.as_view(template_name='password_reset_confirm.html'),
         name='password_reset_confirm'),
    path('reset/done/',
         auth_views.PasswordResetCompleteView.as_view(template_name='password_reset_complete.html'),
         name='password_reset_complete'),
]
