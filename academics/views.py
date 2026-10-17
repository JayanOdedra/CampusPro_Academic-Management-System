import json
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.models import User, Group
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.views.decorators.http import require_POST
from django.db.models import Count, Avg, Sum, Q
from django.core.paginator import Paginator, InvalidPage

import csv
import math
from datetime import timedelta
from django.http import HttpResponse
from django.utils import timezone as dj_timezone

from .forms import RegistrationForm, CourseForm, EnrollmentForm, AttendanceDateForm, MarksUploadForm, NoticeForm
from .models import Profile, Course, Enrollment, Attendance, Marks, LoginAttempt
from .decorators import role_required
from .utils import (
    attendance_percentage, grade_from_percentage, marks_summary,
    course_attendance_avg, course_marks_avg, csv_safe,
)
from .audit import (
    log_action,
    REGISTER, USER_APPROVED, USER_REJECTED,
    COURSE_CREATED, COURSE_EDITED, COURSE_DELETED,
    TEACHER_ASSIGNED, STUDENT_ENROLLED, STUDENT_UNENROLLED,
    ATTENDANCE_MARKED, MARKS_UPLOADED,
    NOTICE_POSTED, NOTICE_DELETED,
    LOGIN_LOCKED, EXPORT_CSV, REPORT_CARD_VIEWED, ACCOUNT_UNLOCKED,
)
from .emails import (
    send_registration_email, send_approval_email,
    send_rejection_email, send_marks_notification,
)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------
def register_view(request):
    """
    Handles new user registration.
    GET  → show blank form.
    POST → validate, save User+Profile (is_approved=False), redirect to login.
    """
    if request.method == 'POST':
        form = RegistrationForm(request.POST)
        if form.is_valid():
            new_user = form.save()  # creates User and Profile (see forms.py)
            # FIX: emails.py existed but was never called. Never raises (see emails.py).
            send_registration_email(new_user)
            # Log the registration event — target is the new username
            log_action(request, REGISTER, target=form.cleaned_data['username'])
            messages.success(
                request,
                "Registered successfully. Please wait for admin approval."
            )
            return redirect('login')
    else:
        form = RegistrationForm()
    return render(request, 'register.html', {'form': form})


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------
def _client_ip(request):
    """IP of the client. REMOTE_ADDR is set by the server, so it cannot be faked by the browser."""
    return request.META.get('REMOTE_ADDR') or '0.0.0.0'


def _active_lock(username):
    """
    FIX (Phase 5): lockout was only a model before; now it is really used.
    Returns the LoginAttempt row that currently locks this username, or None.
    The lock is per USERNAME (any IP), so a locked account stays locked
    even if the attacker changes IP address.
    """
    return LoginAttempt.objects.filter(
        username=username,
        locked_until__gt=dj_timezone.now(),
    ).order_by('-locked_until').first()


def _record_failed_login(request, username):
    """
    Count one failed login for (username, ip). When the count reaches
    LoginAttempt.MAX_ATTEMPTS the account is locked for LOCK_MINUTES.
    Works for ANY username string (even one that does not exist), so the
    response never reveals whether an account exists.
    Returns True if this failure caused a new lock.
    """
    attempt, _ = LoginAttempt.objects.get_or_create(
        username=username, ip_address=_client_ip(request),
    )
    attempt.failed_count += 1
    locked_now = False
    if attempt.failed_count >= LoginAttempt.MAX_ATTEMPTS:
        attempt.locked_until = dj_timezone.now() + timedelta(minutes=LoginAttempt.LOCK_MINUTES)
        attempt.failed_count = 0  # fresh count once the lock expires
        locked_now = True
    attempt.save()
    return locked_now


def login_view(request):
    """
    Custom login view using Django's built-in AuthenticationForm.
    Order of checks:
      1. Is the username locked after too many failures?  -> block, even for a correct password.
      2. Are the credentials valid?                       -> if not, count a failure.
      3. Is the account approved by the admin?            -> if not, block.
      4. Otherwise log the user in and clear the failure counter.
    Superusers skip only the approval check (they are still lockout-protected).
    """
    if request.method == 'POST':
        username = request.POST.get('username', '').strip()[:150]

        # 1. Lockout check happens BEFORE authentication
        lock = _active_lock(username) if username else None
        if lock:
            seconds_left = (lock.locked_until - dj_timezone.now()).total_seconds()
            minutes_left = max(1, math.ceil(seconds_left / 60))
            messages.error(
                request,
                f"Too many failed attempts. Try again in {minutes_left} minute(s)."
            )
            return render(request, 'login.html', {'form': AuthenticationForm()})

        form = AuthenticationForm(request, data=request.POST)
        if form.is_valid():
            user = form.get_user()  # the authenticated User object

            # Correct credentials: clear the failure counter for this username
            LoginAttempt.objects.filter(username=username).delete()

            # Superusers can always log in — no Profile check needed
            if user.is_superuser:
                login(request, user)
                return redirect('dashboard')

            # For regular users, check the approval flag on their Profile
            try:
                profile = user.profile
            except Profile.DoesNotExist:
                messages.error(request, "Account setup incomplete. Contact admin.")
                return render(request, 'login.html', {'form': form})

            if not profile.is_approved:
                # Credentials are correct but account not yet approved
                messages.error(request, "Your account is pending admin approval.")
                return render(request, 'login.html', {'form': form})

            # All checks passed — log the user in
            login(request, user)
            return redirect('dashboard')

        # 2. Invalid credentials: count the failure (only if a username was typed)
        if username and _record_failed_login(request, username):
            log_action(request, LOGIN_LOCKED, target=username,
                       details=f"Locked for {LoginAttempt.LOCK_MINUTES} minutes")
            messages.error(
                request,
                f"Too many failed attempts. Account locked for {LoginAttempt.LOCK_MINUTES} minutes."
            )
        # FIX: no extra messages.error here — AuthenticationForm already shows its own
        # "incorrect username or password" error, so the user no longer sees it twice.
    else:
        form = AuthenticationForm()
    return render(request, 'login.html', {'form': form})


# ---------------------------------------------------------------------------
# Logout (POST only — GET logout is a CSRF risk)
# ---------------------------------------------------------------------------
@require_POST
def logout_view(request):
    """Logs the user out and redirects to login. POST-only for CSRF safety."""
    logout(request)
    return redirect('login')


# ---------------------------------------------------------------------------
# Dashboard router — decides where to send each role after login
# ---------------------------------------------------------------------------
@login_required
def dashboard(request):
    """
    Central dispatcher: reads the user's role and redirects to the
    correct dashboard. No content is rendered here directly.
    """
    if request.user.is_superuser:
        return redirect('admin_dashboard')
    try:
        role = request.user.profile.role
    except Profile.DoesNotExist:
        return redirect('login')

    if role == 'teacher':
        return redirect('teacher_dashboard')
    elif role == 'student':
        return redirect('student_dashboard')
    # Fallback — should never reach here
    return redirect('login')


# ---------------------------------------------------------------------------
# Role-specific dashboards (placeholder content for Phase 1)
# ---------------------------------------------------------------------------
@role_required('teacher')
def teacher_dashboard(request):
    """
    Phase 3: Teacher dashboard with stat cards, per-course attendance bar charts,
    and an at-risk students list (attendance < 75%).
    Security: all queries are filtered by request.user — never a URL param.
    """
    # Only courses assigned to THIS teacher
    courses = Course.objects.filter(
        teacher=request.user
    ).prefetch_related('enrollments__student', 'enrollments__attendances')

    total_students = sum(c.enrollments.count() for c in courses)

    # Build per-course chart data and at-risk list in one pass
    course_charts = []   # list of dicts, one per course
    at_risk = []         # students with attendance < 75%

    for course in courses:
        enrollments = course.enrollments.all()
        labels = []
        values = []
        for enrollment in enrollments:
            pct = attendance_percentage(enrollment)  # from utils.py
            labels.append(enrollment.student.username)
            values.append(pct)
            if pct < 75:
                at_risk.append({
                    'student': enrollment.student.username,
                    'course':  course.code,
                    'pct':     pct,
                })
        course_charts.append({
            'course':  course,
            # json.dumps is right HERE: the teacher template reads these from data-attributes
            'labels':  json.dumps(labels),
            'values':  json.dumps(values),
            'has_data': bool(labels),
        })

    return render(request, 'teacher_dashboard.html', {
        'courses':       courses,
        'total_students': total_students,
        'course_charts': course_charts,
        'at_risk':       at_risk,
    })


@role_required('student')
def student_dashboard(request):
    """
    Phase 3: Student dashboard with stat cards and three charts.
    Security: all data is scoped to request.user — no URL ids used.
    """
    enrollments = Enrollment.objects.filter(
        student=request.user
    ).select_related('course').prefetch_related('attendances', 'marks')

    # --- Stat cards ---
    enrolled_count = enrollments.count()

    # Overall attendance across all courses
    all_att_pcts = [attendance_percentage(e) for e in enrollments]
    overall_att = round(sum(all_att_pcts) / len(all_att_pcts), 1) if all_att_pcts else 0

    # Overall marks across all courses
    total_score = total_max = 0
    for e in enrollments:
        result = e.marks.aggregate(s=Sum('score'), m=Sum('max_score'))
        total_score += result['s'] or 0
        total_max   += result['m'] or 0
    overall_marks = round((total_score / total_max) * 100, 1) if total_max > 0 else 0

    # --- Chart 1: attendance % per course (bar) ---
    att_labels = [e.course.code for e in enrollments]
    att_values = all_att_pcts

    # --- Chart 2: marks % per course (bar) ---
    marks_labels = []
    marks_values = []
    for e in enrollments:
        pct, _ = marks_summary(e)   # from utils.py
        marks_labels.append(e.course.code)
        marks_values.append(pct if pct is not None else 0)

    # --- Chart 3: score trend across ALL exams ordered by exam_name (line) ---
    # Fetch all marks for this student, ordered by exam name for a consistent axis
    all_marks = Marks.objects.filter(
        enrollment__student=request.user
    ).select_related('enrollment__course').order_by('exam_name')
    trend_labels = [f"{m.enrollment.course.code}: {m.exam_name}" for m in all_marks]
    trend_values = [
        round((m.score / m.max_score) * 100, 1) if m.max_score > 0 else 0
        for m in all_marks
    ]

    # --- Low-attendance alert courses ---
    low_att_courses = [
        {'code': e.course.code, 'pct': pct}
        for e, pct in zip(enrollments, all_att_pcts)
        if pct < 75
    ]

    return render(request, 'student_dashboard.html', {
        'enrollments':     enrollments,
        'enrolled_count':  enrolled_count,
        'overall_att':     overall_att,
        'overall_marks':   overall_marks,
        'att_labels':      att_labels,
        'att_values':      att_values,
        'marks_labels':    marks_labels,
        'marks_values':    marks_values,
        'trend_labels':    trend_labels,
        'trend_values':    trend_values,
        'low_att_courses': low_att_courses,
    })


# ---------------------------------------------------------------------------
# Admin panel — superuser only
# ---------------------------------------------------------------------------
@login_required
def admin_dashboard(request):
    """
    Phase 3: Admin dashboard with stat cards and four charts.
    All chart data is computed in Python and passed to the template via
    json_script — a Django template tag that safely serialises Python data
    to JSON inside a <script type="application/json"> tag, preventing XSS.
    """
    if not request.user.is_superuser:
        return render(request, '403.html', status=403)

    student_group = Group.objects.get(name='Student')
    teacher_group = Group.objects.get(name='Teacher')

    # --- Stat card counts (each is a single SQL COUNT query) ---
    total_students = User.objects.filter(groups=student_group).count()
    total_teachers = User.objects.filter(groups=teacher_group).count()
    total_courses  = Course.objects.count()
    pending_count  = Profile.objects.filter(is_approved=False).count()

    # --- Chart 1: students enrolled per course ---
    # annotate() adds a computed field (enroll_count) to each Course row.
    # This is ONE SQL query with a GROUP BY, not a loop.
    courses_annotated = Course.objects.annotate(
        enroll_count=Count('enrollments')
    ).order_by('code')
    chart1_labels = [c.code for c in courses_annotated]   # X-axis: course codes
    chart1_values = [c.enroll_count for c in courses_annotated]

    # --- Chart 2: approved vs pending users (doughnut) ---
    approved_count = Profile.objects.filter(is_approved=True).count()
    chart2_labels = ['Approved', 'Pending']
    chart2_values = [approved_count, pending_count]

    # --- Chart 3: average attendance % per course ---
    # course_attendance_avg() is defined in utils.py
    chart3_labels = [c.code for c in courses_annotated]
    chart3_values = [course_attendance_avg(c) for c in courses_annotated]

    # --- Chart 4: average marks % per course ---
    chart4_labels = [c.code for c in courses_annotated]
    chart4_values = [course_marks_avg(c) for c in courses_annotated]

    # --- 5 most recently registered users (non-superusers) ---
    recent_users = Profile.objects.select_related('user').order_by('-created_at')[:5]

    return render(request, 'admin_dashboard.html', {
        'total_students': total_students,
        'total_teachers': total_teachers,
        'total_courses':  total_courses,
        'pending_count':  pending_count,
        # stat_cards is a list of tuples: (bootstrap-icon-name, label, value, color)
        # The template loops over this to render each card without repeating HTML
        'stat_cards': [
            ('people-fill',          'Total Students',    total_students, 'primary'),
            ('person-workspace',     'Total Teachers',    total_teachers, 'info'),
            ('journal-bookmark-fill','Total Courses',     total_courses,  'success'),
            ('hourglass-split',      'Pending Approvals', pending_count,  'warning'),
        ],
        # json.dumps converts Python lists to JSON strings;
        # the template wraps them in json_script for safe embedding
        'chart1_labels': chart1_labels,
        'chart1_values': chart1_values,
        'chart2_labels': chart2_labels,
        'chart2_values': chart2_values,
        'chart3_labels': chart3_labels,
        'chart3_values': chart3_values,
        'chart4_labels': chart4_labels,
        'chart4_values': chart4_values,
        'recent_users':  recent_users,
    })


@login_required
def pending_users(request):
    """
    Phase 3: Lists pending users with search, role filter, and pagination.
    Also handles approve/reject POST actions (unchanged from Phase 1).
    """
    if not request.user.is_superuser:
        return render(request, '403.html', status=403)

    if request.method == 'POST':
        user_id = request.POST.get('user_id')
        action  = request.POST.get('action')
        try:
            target_user = User.objects.get(pk=user_id)
        except User.DoesNotExist:
            messages.error(request, "User not found.")
            return redirect('pending_users')

        if action == 'approve':
            profile = target_user.profile
            profile.is_approved = True
            profile.save()
            group_name = profile.role.capitalize()
            group = Group.objects.get(name=group_name)
            target_user.groups.add(group)
            send_approval_email(target_user)  # FIX: email was never sent before
            # Log approval with the approved user's username as target
            log_action(request, USER_APPROVED, target=target_user.username,
                       details=f"Role: {profile.role}")
            messages.success(request, f"{target_user.username} approved as {profile.role}.")
        elif action == 'reject':
            username = target_user.username
            email = target_user.email  # save the address BEFORE the user is deleted
            target_user.delete()
            send_rejection_email(username, email)  # FIX: email was never sent before
            # Log rejection — user is already deleted so we use the saved username
            log_action(request, USER_REJECTED, target=username)
            messages.warning(request, f"{username} rejected and deleted.")
        return redirect('pending_users')

    # GET: read search/filter params from the URL
    pending = Profile.objects.filter(is_approved=False).select_related('user')
    return render(request, 'pending_users.html', {'pending': pending})


# ===========================================================================
# PHASE 2 — ADMIN: Course & Enrollment Management
# ===========================================================================

def _superuser_required(request):
    """Helper: returns a 403 response if the user is not a superuser."""
    if not request.user.is_superuser:
        return render(request, '403.html', status=403)
    return None


@login_required
def course_list(request):
    """
    Phase 3: Admin course list with search (name or code) and pagination.
    Q objects let us combine multiple field lookups with OR logic in one query.
    """
    guard = _superuser_required(request)
    if guard:
        return guard

    q = request.GET.get('q', '').strip()  # search term from the URL query string

    # select_related fetches teacher in the same SQL JOIN (no N+1)
    courses = Course.objects.select_related('teacher').order_by('code')

    if q:
        # Q(name__icontains=q) | Q(code__icontains=q) means:
        # "WHERE name LIKE %q% OR code LIKE %q%" — case-insensitive
        courses = courses.filter(Q(name__icontains=q) | Q(code__icontains=q))

    # Paginator splits a queryset into pages of N items each
    paginator = Paginator(courses, 10)  # 10 courses per page
    page_num  = request.GET.get('page', 1)
    try:
        # InvalidPage is raised for non-integer or out-of-range page numbers
        page_obj = paginator.page(page_num)
    except InvalidPage:
        # Gracefully fall back to page 1 instead of crashing
        page_obj = paginator.page(1)

    return render(request, 'admin_course_list.html', {
        'page_obj': page_obj,
        'q': q,  # pass back so the search box stays filled
    })


@login_required
def course_add(request):
    """Admin: create a new course."""
    guard = _superuser_required(request)
    if guard:
        return guard
    form = CourseForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        course = form.save()
        # Log course creation; include teacher if assigned
        log_action(request, COURSE_CREATED, target=course.code,
                   details=f"Teacher: {course.teacher.username if course.teacher else 'None'}")
        messages.success(request, "Course created successfully.")
        return redirect('course_list')
    return render(request, 'admin_course_form.html', {'form': form, 'action': 'Add'})


@login_required
def course_edit(request, course_id):
    """Admin: edit an existing course (name, code, teacher assignment)."""
    guard = _superuser_required(request)
    if guard:
        return guard
    # get_object_or_404 returns a 404 page if the course doesn't exist
    course = get_object_or_404(Course, pk=course_id)
    form = CourseForm(request.POST or None, instance=course)
    if request.method == 'POST' and form.is_valid():
        form.save()
        # Log the edit; note old vs new teacher if changed
        log_action(request, COURSE_EDITED, target=course.code,
                   details=f"Teacher: {course.teacher.username if course.teacher else 'None'}")
        messages.success(request, "Course updated successfully.")
        return redirect('course_list')
    return render(request, 'admin_course_form.html', {'form': form, 'action': 'Edit', 'course': course})


@login_required
def course_delete(request, course_id):
    """
    Admin: delete a course.
    GET  → show confirmation page.
    POST → perform the delete.
    This two-step pattern prevents accidental or CSRF-triggered deletions.
    """
    guard = _superuser_required(request)
    if guard:
        return guard
    course = get_object_or_404(Course, pk=course_id)
    if request.method == 'POST':
        code = course.code  # save before deletion
        course.delete()
        log_action(request, COURSE_DELETED, target=code)
        messages.warning(request, f"Course '{code}' has been deleted.")
        return redirect('course_list')
    # GET: show the confirmation page
    return render(request, 'admin_course_confirm_delete.html', {'course': course})


@login_required
def enrollment_manage(request, course_id):
    """
    Admin: enroll or unenroll students in a specific course.
    GET  → show current enrollments + a form to add more.
    POST with action=enroll   → create Enrollment.
    POST with action=unenroll → delete Enrollment.
    """
    guard = _superuser_required(request)
    if guard:
        return guard
    course = get_object_or_404(Course, pk=course_id)

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'enroll':
            student_id = request.POST.get('student_id')
            student = get_object_or_404(User, pk=student_id)
            enrollment, created = Enrollment.objects.get_or_create(
                student=student, course=course
            )
            if created:
                log_action(request, STUDENT_ENROLLED, target=student.username,
                           details=f"Course: {course.code}")
                messages.success(request, f"{student.username} enrolled in {course.code}.")
            else:
                messages.warning(request, f"{student.username} is already enrolled.")

        elif action == 'unenroll':
            enrollment_id = request.POST.get('enrollment_id')
            enrollment = get_object_or_404(Enrollment, pk=enrollment_id, course=course)
            uname = enrollment.student.username
            enrollment.delete()
            log_action(request, STUDENT_UNENROLLED, target=uname,
                       details=f"Course: {course.code}")
            messages.warning(request, "Student unenrolled successfully.")

        return redirect('enrollment_manage', course_id=course_id)

    # Current enrollments for this course
    enrollments = Enrollment.objects.filter(course=course).select_related('student')
    # Students not yet enrolled (to populate the "enroll" dropdown)
    enrolled_ids = enrollments.values_list('student_id', flat=True)
    student_group = Group.objects.get(name='Student')
    available_students = User.objects.filter(
        groups=student_group,
        profile__is_approved=True,
    ).exclude(pk__in=enrolled_ids)

    return render(request, 'admin_enrollment.html', {
        'course': course,
        'enrollments': enrollments,
        'available_students': available_students,
    })


# ===========================================================================
# PHASE 2 — TEACHER: Attendance & Marks
# ===========================================================================

def _teacher_owns_course(request, course):
    """
    Security helper: returns True only if the logged-in teacher is assigned
    to this course. Prevents a teacher from accessing another teacher's course
    by manually changing the course_id in the URL.
    """
    return course.teacher == request.user


@role_required('teacher')
def teacher_attendance(request, course_id):
    """
    Teacher marks attendance for a chosen date.
    Step 1 (GET with no date): show the date-picker form.
    Step 2 (GET with ?date=...): show all enrolled students with checkboxes.
    Step 3 (POST): save or update attendance records for that date.

    If records already exist for that date, they are updated (not duplicated)
    because of the unique_together constraint on (enrollment, date).
    """
    course = get_object_or_404(Course, pk=course_id)
    # Security: 403 if this teacher doesn't own the course
    if not _teacher_owns_course(request, course):
        return render(request, '403.html', status=403)

    enrollments = Enrollment.objects.filter(course=course).select_related('student')
    date_form = AttendanceDateForm(request.GET or None)
    selected_date = None
    existing_attendance = {}  # maps enrollment_id → Attendance object

    if request.method == 'POST':
        selected_date_str = request.POST.get('date')
        try:
            from datetime import date as date_type
            selected_date = date_type.fromisoformat(selected_date_str)
        except (ValueError, TypeError):
            messages.error(request, "Invalid date.")
            return redirect('teacher_attendance', course_id=course_id)

        # For each enrolled student, update_or_create an Attendance record
        # update_or_create: if (enrollment, date) exists → update; else → create
        for enrollment in enrollments:
            # Checkbox is present in POST only if checked (present=True)
            is_present = str(enrollment.pk) in request.POST.getlist('present')
            Attendance.objects.update_or_create(
                enrollment=enrollment,
                date=selected_date,
                defaults={'present': is_present},  # field to set/update
            )
        messages.success(request, f"Attendance saved for {selected_date}.")
        # Log attendance marking: course code + date as details
        log_action(request, ATTENDANCE_MARKED, target=course.code,
                   details=f"Date: {selected_date}")
        return redirect('teacher_attendance', course_id=course_id)

    # GET with ?date= param: pre-load existing attendance for that date
    if date_form.is_valid():
        selected_date = date_form.cleaned_data['date']
        for att in Attendance.objects.filter(enrollment__in=enrollments, date=selected_date):
            existing_attendance[att.enrollment_id] = att

    return render(request, 'teacher_attendance.html', {
        'course': course,
        'enrollments': enrollments,
        'date_form': date_form,
        'selected_date': selected_date,
        'existing_attendance': existing_attendance,
    })


@role_required('teacher')
def teacher_marks(request, course_id):
    """
    Teacher uploads marks for a named exam.
    Step 1 (GET): show exam name + max_score form.
    Step 2 (POST): for each enrolled student, save or update their score.

    update_or_create on (enrollment, exam_name) prevents duplicate exam rows.
    Score validation happens in the view before saving.
    """
    course = get_object_or_404(Course, pk=course_id)
    if not _teacher_owns_course(request, course):
        return render(request, '403.html', status=403)

    enrollments = Enrollment.objects.filter(course=course).select_related('student')
    form = MarksUploadForm(request.POST or None)

    if request.method == 'POST' and form.is_valid():
        exam_name = form.cleaned_data['exam_name']
        max_score = form.cleaned_data['max_score']
        errors = []
        saved = []  # FIX: remember saved rows so each student can be emailed

        for enrollment in enrollments:
            # Score input name is "score_<enrollment_pk>" in the template
            raw = request.POST.get(f'score_{enrollment.pk}', '').strip()
            if raw == '':
                continue  # skip students with no score entered
            try:
                score = float(raw)
            except ValueError:
                errors.append(f"Invalid score for {enrollment.student.username}.")
                continue
            # Validate range before saving
            if score < 0 or score > max_score:
                errors.append(
                    f"{enrollment.student.username}: score {score} is out of range "
                    f"(0–{max_score})."
                )
                continue
            # update_or_create: if this student already has a mark for this exam, update it
            Marks.objects.update_or_create(
                enrollment=enrollment,
                exam_name=exam_name,
                defaults={'score': score, 'max_score': max_score},
            )
            saved.append((enrollment.student, score))

        # FIX: notify students (send_marks_notification never raises)
        for student, score in saved:
            if student.email:
                send_marks_notification(student, course, exam_name, score, max_score)

        if errors:
            for err in errors:
                messages.error(request, err)
        else:
            log_action(request, MARKS_UPLOADED, target=course.code,
                       details=f"Exam: {exam_name}, Max: {max_score}")
            messages.success(request, f"Marks saved for '{exam_name}'.")
        return redirect('teacher_marks', course_id=course_id)

    # Also show existing marks for this course grouped by exam
    all_marks = Marks.objects.filter(
        enrollment__course=course
    ).select_related('enrollment__student').order_by('exam_name')

    return render(request, 'teacher_marks.html', {
        'course': course,
        'enrollments': enrollments,
        'form': form,
        'all_marks': all_marks,
    })


# ===========================================================================
# PHASE 2 — STUDENT: Attendance & Marks views
# ===========================================================================

@role_required('student')
def student_attendance(request, course_id):
    """
    Student views their own attendance for one course.
    We fetch the enrollment using request.user — never a URL student_id —
    so a student cannot see another student's data by changing the URL.
    """
    course = get_object_or_404(Course, pk=course_id)
    # This will 404 if the student is not enrolled, preventing data leakage
    enrollment = get_object_or_404(Enrollment, student=request.user, course=course)

    attendances = Attendance.objects.filter(enrollment=enrollment).order_by('date')
    total = attendances.count()                          # total classes held
    attended = attendances.filter(present=True).count() # classes student was present
    # Percentage: avoid division by zero if no classes have been held yet
    percentage = round((attended / total) * 100, 1) if total > 0 else 0

    return render(request, 'student_attendance.html', {
        'course': course,
        'attendances': attendances,
        'total': total,
        'attended': attended,
        'percentage': percentage,
    })


@role_required('student')
def student_marks(request, course_id):
    """
    Student views their own marks for one course.
    Grade is calculated from the overall percentage across all exams.
    """
    course = get_object_or_404(Course, pk=course_id)
    enrollment = get_object_or_404(Enrollment, student=request.user, course=course)

    marks_qs = Marks.objects.filter(enrollment=enrollment).order_by('exam_name')

    # Build per-exam rows with percentage
    marks_data = []
    total_score = 0
    total_max = 0
    for m in marks_qs:
        pct = round((m.score / m.max_score) * 100, 1) if m.max_score > 0 else 0
        marks_data.append({
            'exam_name': m.exam_name,
            'score': m.score,
            'max_score': m.max_score,
            'percentage': pct,
        })
        total_score += m.score
        total_max += m.max_score

    # Overall percentage across all exams
    overall_pct = round((total_score / total_max) * 100, 1) if total_max > 0 else None

    # Grade thresholds
    if overall_pct is None:
        grade = 'N/A'
    elif overall_pct >= 80:
        grade = 'A'
    elif overall_pct >= 65:
        grade = 'B'
    elif overall_pct >= 50:
        grade = 'C'
    elif overall_pct >= 40:
        grade = 'D'
    else:
        grade = 'Fail'

    return render(request, 'student_marks.html', {
        'course': course,
        'marks_data': marks_data,
        'overall_pct': overall_pct,
        'grade': grade,
    })


# ===========================================================================
# PHASE 3 — ADMIN: Users list with search, filter, pagination
# ===========================================================================

@login_required
def users_list(request):
    """
    Admin: searchable, filterable, paginated list of ALL registered users
    (excluding superusers).

    Search: username or email (Q objects with OR).
    Filter: role (student/teacher) and approval status.
    Pagination: 10 per page; invalid page numbers fall back to page 1.
    """
    guard = _superuser_required(request)
    if guard:
        return guard

    # Read filter params from the URL query string
    q        = request.GET.get('q', '').strip()       # search term
    role     = request.GET.get('role', '').strip()    # 'student' | 'teacher' | ''
    approved = request.GET.get('approved', '').strip() # '1' | '0' | ''

    # Start with all non-superuser profiles, join User in the same query
    profiles = Profile.objects.select_related('user').filter(
        user__is_superuser=False
    ).order_by('-created_at')

    # Apply search: match username OR email, case-insensitive
    if q:
        profiles = profiles.filter(
            Q(user__username__icontains=q) | Q(user__email__icontains=q)
        )

    # Apply role filter
    if role in ('student', 'teacher'):
        profiles = profiles.filter(role=role)

    # Apply approval filter
    if approved == '1':
        profiles = profiles.filter(is_approved=True)
    elif approved == '0':
        profiles = profiles.filter(is_approved=False)

    paginator = Paginator(profiles, 10)
    page_num  = request.GET.get('page', 1)
    try:
        page_obj = paginator.page(page_num)
    except InvalidPage:
        page_obj = paginator.page(1)

    return render(request, 'admin_users_list.html', {
        'page_obj': page_obj,
        'q':        q,
        'role':     role,
        'approved': approved,
    })


# ===========================================================================
# PHASE 3 — TEACHER: Students list for a course with search + pagination
# ===========================================================================

@role_required('teacher')
def teacher_course_students(request, course_id):
    """
    Teacher: paginated list of students enrolled in one of their courses,
    with a search box (by username) and per-student attendance/marks summary.
    Security: 403 if the course is not assigned to request.user.
    """
    course = get_object_or_404(Course, pk=course_id)
    if not _teacher_owns_course(request, course):
        return render(request, '403.html', status=403)

    q = request.GET.get('q', '').strip()

    # Fetch enrollments with student data in one query
    enrollments = Enrollment.objects.filter(
        course=course
    ).select_related('student').prefetch_related('attendances', 'marks')

    if q:
        # Filter by student username
        enrollments = enrollments.filter(student__username__icontains=q)

    paginator = Paginator(enrollments, 10)
    page_num  = request.GET.get('page', 1)
    try:
        page_obj = paginator.page(page_num)
    except InvalidPage:
        page_obj = paginator.page(1)

    # Build summary rows for the current page only (avoids computing for all students)
    rows = []
    for enrollment in page_obj.object_list:
        att_pct        = attendance_percentage(enrollment)
        marks_pct, grade = marks_summary(enrollment)
        rows.append({
            'enrollment': enrollment,
            'att_pct':    att_pct,
            'marks_pct':  marks_pct,
            'grade':      grade,
            'at_risk':    att_pct < 75,
        })

    return render(request, 'teacher_course_students.html', {
        'course':   course,
        'page_obj': page_obj,
        'rows':     rows,
        'q':        q,
    })


# ===========================================================================
# PHASE 3 — STUDENT: Filtered attendance + marks history with pagination
# ===========================================================================

@role_required('student')
def student_history(request):
    """
    Student: view all attendance and marks records across all their courses,
    with a filter by course and pagination.
    Security: always scoped to request.user.
    """
    # All enrollments for this student
    all_enrollments = Enrollment.objects.filter(
        student=request.user
    ).select_related('course')

    course_filter = request.GET.get('course', '').strip()  # course pk as string

    # Attendance records
    att_qs = Attendance.objects.filter(
        enrollment__student=request.user
    ).select_related('enrollment__course').order_by('-date')

    # Marks records
    marks_qs = Marks.objects.filter(
        enrollment__student=request.user
    ).select_related('enrollment__course').order_by('enrollment__course__code', 'exam_name')

    # Apply course filter if a valid course pk was provided
    if course_filter:
        att_qs   = att_qs.filter(enrollment__course__pk=course_filter)
        marks_qs = marks_qs.filter(enrollment__course__pk=course_filter)

    # Paginate attendance (10 per page)
    att_paginator = Paginator(att_qs, 10)
    att_page_num  = request.GET.get('att_page', 1)
    try:
        att_page = att_paginator.page(att_page_num)
    except InvalidPage:
        att_page = att_paginator.page(1)

    # Paginate marks (10 per page)
    marks_paginator = Paginator(marks_qs, 10)
    marks_page_num  = request.GET.get('marks_page', 1)
    try:
        marks_page = marks_paginator.page(marks_page_num)
    except InvalidPage:
        marks_page = marks_paginator.page(1)

    return render(request, 'student_history.html', {
        'all_enrollments': all_enrollments,   # for the filter dropdown
        'course_filter':   course_filter,
        'att_page':        att_page,
        'marks_page':      marks_page,
    })


# ===========================================================================
# PHASE 4 — ADMIN: Audit Log viewer
# ===========================================================================

@login_required
def audit_log(request):
    """
    Admin-only page showing MongoDB audit logs with search, action filter,
    date-range filter, pagination (20 per page using skip/limit), and a
    Chart.js bar chart of logs per action for the last 7 days.

    NoSQL injection prevention:
    - We NEVER pass raw user input directly as a MongoDB query value.
    - Each filter value is validated/whitelisted before being added to the
      filter dict. Strings are used only in regex or exact-match contexts
      where pymongo handles escaping internally.
    """
    if not request.user.is_superuser:
        return render(request, '403.html', status=403)

    from .mongo import get_audit_collection
    from datetime import datetime, timezone, timedelta
    import re
    from bson import ObjectId

    col = get_audit_collection()

    # Read filter params — all are plain strings from GET
    q          = request.GET.get('q', '').strip()          # username search
    action_f   = request.GET.get('action', '').strip()     # exact action filter
    date_from  = request.GET.get('date_from', '').strip()  # YYYY-MM-DD
    date_to    = request.GET.get('date_to', '').strip()    # YYYY-MM-DD
    page_num   = request.GET.get('page', '1')

    # Safe page number: must be a positive integer
    try:
        page_num = max(1, int(page_num))
    except ValueError:
        page_num = 1

    PER_PAGE = 20

    # Build the MongoDB filter dict from validated values only
    mongo_filter = {}

    if q:
        # re.escape prevents any regex metacharacters in the username from
        # being interpreted as regex operators — this is the safe way to do
        # a "contains" search in MongoDB without NoSQL injection risk
        mongo_filter['username'] = {'$regex': re.escape(q), '$options': 'i'}

    # Whitelist: only accept known action strings, never raw user input
    VALID_ACTIONS = {
        'REGISTER', 'USER_APPROVED', 'USER_REJECTED',
        'LOGIN_SUCCESS', 'LOGIN_FAILED', 'LOGOUT',
        'COURSE_CREATED', 'COURSE_EDITED', 'COURSE_DELETED',
        'TEACHER_ASSIGNED', 'STUDENT_ENROLLED', 'STUDENT_UNENROLLED',
        'ATTENDANCE_MARKED', 'MARKS_UPLOADED',
        'NOTICE_POSTED', 'NOTICE_DELETED', 'ACCESS_DENIED',
        'LOGIN_LOCKED', 'ACCOUNT_UNLOCKED', 'EXPORT_CSV', 'REPORT_CARD_VIEWED',
    }
    if action_f in VALID_ACTIONS:
        # Exact match — safe because we validated against a whitelist
        mongo_filter['action'] = action_f

    # Date range filter: parse ISO dates and build a $gte/$lte range
    ts_filter = {}
    if date_from:
        try:
            ts_filter['$gte'] = datetime.strptime(date_from, '%Y-%m-%d').replace(
                tzinfo=timezone.utc)
        except ValueError:
            pass  # ignore invalid date input
    if date_to:
        try:
            # Add 1 day so "to" is inclusive of the whole day
            ts_filter['$lte'] = datetime.strptime(date_to, '%Y-%m-%d').replace(
                tzinfo=timezone.utc) + timedelta(days=1)
        except ValueError:
            pass
    if ts_filter:
        mongo_filter['timestamp'] = ts_filter

    # Default empty results if Mongo is unavailable
    logs = []
    total = 0
    chart_labels = []
    chart_values = []

    if col is not None:
        try:
            # Count total matching docs for pagination
            total = col.count_documents(mongo_filter)

            # skip/limit is MongoDB's pagination mechanism:
            # skip(n) skips the first n documents, limit(m) returns at most m
            skip = (page_num - 1) * PER_PAGE
            cursor = col.find(mongo_filter).sort(
                'timestamp', -1          # -1 = DESCENDING (newest first)
            ).skip(skip).limit(PER_PAGE)
            logs = list(cursor)

            # --- Chart: logs per action for the last 7 days ---
            seven_days_ago = datetime.now(timezone.utc) - timedelta(days=7)
            # MongoDB aggregation pipeline:
            # $match: filter to last 7 days
            # $group: group by action field, count documents in each group
            pipeline = [
                {'$match': {'timestamp': {'$gte': seven_days_ago}}},
                {'$group': {'_id': '$action', 'count': {'$sum': 1}}},
                {'$sort': {'count': -1}},
            ]
            chart_data = list(col.aggregate(pipeline))
            chart_labels = [d['_id'] for d in chart_data]
            chart_values = [d['count'] for d in chart_data]
        except Exception as exc:
            import logging as _log
            _log.getLogger(__name__).warning("Audit log query failed: %s", exc)

    # Total pages for pagination controls
    import math
    total_pages = math.ceil(total / PER_PAGE) if total > 0 else 1

    return render(request, 'admin_audit_log.html', {
        'logs':          logs,
        'total':         total,
        'page_num':      page_num,
        'total_pages':   total_pages,
        'per_page':      PER_PAGE,
        'q':             q,
        'action_f':      action_f,
        'date_from':     date_from,
        'date_to':       date_to,
        'valid_actions': sorted(VALID_ACTIONS),
        'chart_labels':  chart_labels,
        'chart_values':  chart_values,
        'mongo_ok':      col is not None,
    })


@login_required
def audit_log_csv(request):
    """
    Export the currently filtered audit logs as a CSV file.
    Uses Python's built-in csv module — no extra library needed.
    The same filter logic as audit_log() is applied so the export
    matches exactly what the user sees on screen.
    """
    if not request.user.is_superuser:
        return render(request, '403.html', status=403)

    from .mongo import get_audit_collection
    from datetime import datetime, timezone, timedelta
    import re

    col = get_audit_collection()

    q         = request.GET.get('q', '').strip()
    action_f  = request.GET.get('action', '').strip()
    date_from = request.GET.get('date_from', '').strip()
    date_to   = request.GET.get('date_to', '').strip()

    VALID_ACTIONS = {
        'REGISTER', 'USER_APPROVED', 'USER_REJECTED',
        'LOGIN_SUCCESS', 'LOGIN_FAILED', 'LOGOUT',
        'COURSE_CREATED', 'COURSE_EDITED', 'COURSE_DELETED',
        'TEACHER_ASSIGNED', 'STUDENT_ENROLLED', 'STUDENT_UNENROLLED',
        'ATTENDANCE_MARKED', 'MARKS_UPLOADED',
        'NOTICE_POSTED', 'NOTICE_DELETED', 'ACCESS_DENIED',
        'LOGIN_LOCKED', 'ACCOUNT_UNLOCKED', 'EXPORT_CSV', 'REPORT_CARD_VIEWED',
    }

    mongo_filter = {}
    if q:
        mongo_filter['username'] = {'$regex': re.escape(q), '$options': 'i'}
    if action_f in VALID_ACTIONS:
        mongo_filter['action'] = action_f
    ts_filter = {}
    if date_from:
        try:
            ts_filter['$gte'] = datetime.strptime(date_from, '%Y-%m-%d').replace(
                tzinfo=timezone.utc)
        except ValueError:
            pass
    if date_to:
        try:
            ts_filter['$lte'] = datetime.strptime(date_to, '%Y-%m-%d').replace(
                tzinfo=timezone.utc) + timedelta(days=1)
        except ValueError:
            pass
    if ts_filter:
        mongo_filter['timestamp'] = ts_filter

    # HttpResponse with text/csv content type triggers a file download
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="audit_log.csv"'

    writer = csv.writer(response)
    # Header row
    writer.writerow(['Timestamp', 'Username', 'Role', 'Action', 'Target', 'Details', 'IP'])

    if col is not None:
        try:
            # No skip/limit for CSV — export all matching records
            cursor = col.find(mongo_filter).sort('timestamp', -1)
            for doc in cursor:
                # FIX: csv_safe stops spreadsheet formula injection (=, +, -, @)
                writer.writerow([csv_safe(v) for v in (
                    doc.get('timestamp', ''),
                    doc.get('username', ''),
                    doc.get('role', ''),
                    doc.get('action', ''),
                    doc.get('target', ''),
                    doc.get('details', ''),
                    doc.get('ip_address', ''),
                )])
        except Exception as exc:
            import logging as _log
            _log.getLogger(__name__).warning("Audit CSV export failed: %s", exc)

    return response


# ===========================================================================
# PHASE 4 — NOTICES: Post, list, delete
# ===========================================================================

def _get_notices_for_user(user, limit=None):
    """
    Helper: fetch notices visible to a given user from MongoDB.
    Audience rules:
      - 'all'      → everyone sees it
      - 'students' → only students (and admin) see it
      - 'teachers' → only teachers (and admin) see it
    Returns a list of dicts (MongoDB documents), newest first.
    Returns [] if Mongo is unavailable.
    """
    from .mongo import get_notices_collection
    col = get_notices_collection()
    if col is None:
        return []

    # Build the audience filter based on the user's role
    if user.is_superuser:
        audience_filter = {}  # admin sees everything
    else:
        try:
            role = user.profile.role  # 'student' or 'teacher'
        except Exception:
            return []
        # Match notices for 'all' OR for this specific role
        audience_filter = {
            'audience': {'$in': ['all', role + 's']}
            # 'student' → 'students', 'teacher' → 'teachers'
        }

    mongo_filter = {'is_active': True, **audience_filter}

    try:
        cursor = col.find(mongo_filter).sort('created_at', -1)
        if limit:
            cursor = cursor.limit(limit)
        notices = list(cursor)
        # FIX: templates cannot read names starting with '_', so add a plain string 'id'
        for notice in notices:
            notice['id'] = str(notice['_id'])
        return notices
    except Exception as exc:
        import logging as _log
        _log.getLogger(__name__).warning("Notices fetch failed: %s", exc)
        return []


@login_required
def notices_page(request):
    """
    Full notices page — paginated, visible to all logged-in users.
    Students see 'all' + 'students' notices.
    Teachers see 'all' + 'teachers' notices.
    Admin sees everything.
    Pagination is done in Python (not MongoDB skip/limit) for simplicity
    since notice counts are small.
    """
    all_notices = _get_notices_for_user(request.user)

    # Django Paginator works on any list, not just querysets
    paginator = Paginator(all_notices, 10)
    page_num  = request.GET.get('page', 1)
    try:
        page_obj = paginator.page(page_num)
    except InvalidPage:
        page_obj = paginator.page(1)

    return render(request, 'notices_page.html', {'page_obj': page_obj})


@login_required
def notice_post(request):
    """
    Post a new notice.
    - Admin can post to any audience ('all', 'students', 'teachers').
    - Teachers can only post to 'students'.
    - Students cannot post notices (403).
    """
    # Students are not allowed to post
    if not request.user.is_superuser:
        try:
            role = request.user.profile.role
        except Exception:
            return render(request, '403.html', status=403)
        if role == 'student':
            return render(request, '403.html', status=403)

    form = NoticeForm(request.POST or None, user=request.user)

    if request.method == 'POST' and form.is_valid():
        from .mongo import get_notices_collection
        from datetime import datetime, timezone
        col = get_notices_collection()

        if col is None:
            messages.error(request, "Notice board is unavailable (MongoDB is down).")
            return redirect('notices_page')

        doc = {
            'title':      form.cleaned_data['title'],
            'message':    form.cleaned_data['message'],
            'audience':   form.cleaned_data['audience'],
            'posted_by':  request.user.username,
            'created_at': datetime.now(timezone.utc),
            'is_active':  True,
        }
        try:
            col.insert_one(doc)
            log_action(request, NOTICE_POSTED, target=doc['title'],
                       details=f"Audience: {doc['audience']}")
            messages.success(request, "Notice posted successfully.")
        except Exception as exc:
            import logging as _log
            _log.getLogger(__name__).warning("Notice insert failed: %s", exc)
            messages.error(request, "Failed to post notice.")

        return redirect('notices_page')

    return render(request, 'notice_post.html', {'form': form})


@login_required
def notice_delete(request, notice_id):
    """
    Delete a notice by its MongoDB ObjectId.
    Security:
    - Validate the ObjectId format before querying (prevents crash on bad input).
    - Only the poster or a superuser can delete.
    - POST only (GET cannot delete).
    """
    from .mongo import get_notices_collection
    from bson import ObjectId

    # Validate the ObjectId string before using it in a query
    # bson.ObjectId.is_valid() returns False for malformed ids
    if not ObjectId.is_valid(notice_id):
        # Invalid id format → 404, not a crash
        from django.http import Http404
        raise Http404("Notice not found.")

    col = get_notices_collection()
    if col is None:
        messages.error(request, "Notice board unavailable.")
        return redirect('notices_page')

    try:
        # Find the notice first so we can check ownership
        notice = col.find_one({'_id': ObjectId(notice_id)})
    except Exception:
        from django.http import Http404
        raise Http404("Notice not found.")

    if notice is None:
        from django.http import Http404
        raise Http404("Notice not found.")

    # Only the poster or admin can delete
    if not request.user.is_superuser and notice.get('posted_by') != request.user.username:
        return render(request, '403.html', status=403)

    if request.method == 'POST':
        try:
            col.delete_one({'_id': ObjectId(notice_id)})
            log_action(request, NOTICE_DELETED, target=notice.get('title', ''))
            messages.warning(request, "Notice deleted.")
        except Exception as exc:
            import logging as _log
            _log.getLogger(__name__).warning("Notice delete failed: %s", exc)
            messages.error(request, "Failed to delete notice.")
        return redirect('notices_page')

    # GET: show confirmation page
    return render(request, 'notice_confirm_delete.html', {
        'notice': notice,
        'notice_id': notice_id,
    })


# ===========================================================================
# PHASE 5 — Exports, report card, locked accounts
# ===========================================================================

def _csv_response(filename):
    """Create an empty CSV download response with the given file name."""
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


@role_required('student')
def report_card(request):
    """
    Printable report card for the logged-in student.
    Security: data comes ONLY from request.user — no student id is read from the URL.
    The page has @media print CSS, so the browser's "Print > Save as PDF" gives a clean PDF.
    """
    enrollments = Enrollment.objects.filter(
        student=request.user
    ).select_related('course', 'course__teacher').prefetch_related('marks')

    rows = []
    total_score = total_max = 0
    for e in enrollments:
        marks = list(e.marks.all())  # already prefetched, no extra query
        total_score += sum(m.score for m in marks)
        total_max += sum(m.max_score for m in marks)
        pct, grade = marks_summary(e)
        rows.append({
            'course': e.course,
            'attendance': attendance_percentage(e),
            'marks': marks,
            'percentage': pct,
            'grade': grade,
        })

    overall_pct = round((total_score / total_max) * 100, 1) if total_max > 0 else None
    log_action(request, REPORT_CARD_VIEWED, target=request.user.username)
    return render(request, 'report_card.html', {
        'rows': rows,
        'overall_pct': overall_pct,
        'overall_grade': grade_from_percentage(overall_pct),
        'generated_on': dj_timezone.now(),
    })


@role_required('teacher')
def teacher_attendance_csv(request, course_id):
    """Teacher exports attendance summary of THEIR OWN course (other courses give 403)."""
    course = get_object_or_404(Course, pk=course_id)
    if not _teacher_owns_course(request, course):
        return render(request, '403.html', status=403)

    response = _csv_response(f"attendance_{course.code}.csv")
    writer = csv.writer(response)
    writer.writerow(['Course', 'Student', 'Total classes', 'Present', 'Absent', 'Attendance %'])
    enrollments = Enrollment.objects.filter(course=course).select_related('student').prefetch_related('attendances')
    for e in enrollments:
        records = list(e.attendances.all())
        present = sum(1 for r in records if r.present)
        total = len(records)
        writer.writerow([
            csv_safe(course.code), csv_safe(e.student.username),
            total, present, total - present, attendance_percentage(e),
        ])
    log_action(request, EXPORT_CSV, target=course.code, details="Attendance export")
    return response


@login_required
def users_csv(request):
    """Admin exports all users as CSV."""
    if not request.user.is_superuser:
        return render(request, '403.html', status=403)
    response = _csv_response("users.csv")
    writer = csv.writer(response)
    writer.writerow(['Username', 'Email', 'Role', 'Approved', 'Joined'])
    for u in User.objects.select_related('profile').order_by('username'):
        profile = getattr(u, 'profile', None)
        role = 'admin' if u.is_superuser else (profile.role if profile else 'unknown')
        approved = 'yes' if (u.is_superuser or (profile and profile.is_approved)) else 'no'
        writer.writerow([csv_safe(u.username), csv_safe(u.email), role, approved,
                         u.date_joined.strftime('%Y-%m-%d')])
    log_action(request, EXPORT_CSV, target='users')
    return response


@login_required
def courses_csv(request):
    """Admin exports all courses as CSV."""
    if not request.user.is_superuser:
        return render(request, '403.html', status=403)
    response = _csv_response("courses.csv")
    writer = csv.writer(response)
    writer.writerow(['Code', 'Name', 'Teacher', 'Students enrolled'])
    for c in Course.objects.select_related('teacher').annotate(n=Count('enrollments')).order_by('code'):
        writer.writerow([csv_safe(c.code), csv_safe(c.name),
                         csv_safe(c.teacher.username if c.teacher else ''), c.n])
    log_action(request, EXPORT_CSV, target='courses')
    return response


@login_required
def locked_accounts(request):
    """Admin sees every username that is currently locked, with an Unlock button."""
    if not request.user.is_superuser:
        return render(request, '403.html', status=403)
    locked = LoginAttempt.objects.filter(
        locked_until__gt=dj_timezone.now()
    ).order_by('-locked_until')
    return render(request, 'locked_accounts.html', {'locked': locked})


@login_required
@require_POST
def unlock_account(request, attempt_id):
    """Admin unlocks a username (POST only, CSRF-protected)."""
    if not request.user.is_superuser:
        return render(request, '403.html', status=403)
    attempt = get_object_or_404(LoginAttempt, pk=attempt_id)
    username = attempt.username
    LoginAttempt.objects.filter(username=username).delete()  # clears lock AND counters
    log_action(request, ACCOUNT_UNLOCKED, target=username)
    messages.success(request, f"Account '{username}' unlocked.")
    return redirect('locked_accounts')
