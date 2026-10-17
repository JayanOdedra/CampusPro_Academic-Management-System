"""
academics/tests.py
------------------
Automated tests for the most important flows of CampusPro.
Run with:  python manage.py test

What is covered
  * registration -> blocked until approval -> admin approves -> login works
  * role is validated on the backend (nobody can register as admin)
  * student dashboard + attendance + marks pages load for an enrolled student
    (this is the exact flow that used to crash with NoReverseMatch)
  * role-based access: student cannot open teacher/admin pages,
    teacher cannot open another teacher's course
  * login lockout after 5 wrong passwords (even a correct password is refused)
  * exports (CSV) and the printable report card
  * CSV-injection protection

Tests never touch the real MongoDB: we patch academics.mongo._db to None,
which is exactly the "MongoDB is down" situation the app must survive.
"""

from unittest import mock

from django.contrib.auth.models import User, Group
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import Profile, Course, Enrollment, Attendance, Marks, LoginAttempt
from .utils import csv_safe


# Make the tests independent of .env: no HTTPS redirect, normal cookies.
@override_settings(SECURE_SSL_REDIRECT=False, SESSION_COOKIE_SECURE=False,
                   CSRF_COOKIE_SECURE=False)
class CampusProTests(TestCase):

    def setUp(self):
        # Simulate "MongoDB is down" so tests are fast and never write real logs.
        patcher = mock.patch('academics.mongo._db', None)
        patcher.start()
        self.addCleanup(patcher.stop)

        self.admin = User.objects.create_superuser('admin1', 'admin@example.com', 'AdminPass#123')
        self.teacher = self._make_user('teacher1', 'teacher')
        self.teacher2 = self._make_user('teacher2', 'teacher')
        self.student = self._make_user('student1', 'student')

        self.course = Course.objects.create(name='Python', code='PY101', teacher=self.teacher)
        self.course2 = Course.objects.create(name='Maths', code='MA101', teacher=self.teacher2)
        self.enrollment = Enrollment.objects.create(student=self.student, course=self.course)

    # ---- helpers ---------------------------------------------------------
    def _make_user(self, username, role, approved=True):
        user = User.objects.create_user(username, f'{username}@example.com', 'StrongPass#123')
        Profile.objects.create(user=user, role=role, is_approved=approved)
        if approved:
            user.groups.add(Group.objects.get(name=role.capitalize()))
        return user

    def _login(self, username, password='StrongPass#123'):
        return self.client.post(reverse('login'), {'username': username, 'password': password})

    # ---- registration / approval -----------------------------------------
    def test_register_blocked_until_admin_approves(self):
        resp = self.client.post(reverse('register'), {
            'username': 'newstudent', 'email': 'new@example.com',
            'password1': 'StrongPass#123', 'password2': 'StrongPass#123', 'role': 'student',
        })
        self.assertEqual(resp.status_code, 302)
        user = User.objects.get(username='newstudent')
        self.assertFalse(user.profile.is_approved)

        # Correct password but not approved: login must fail
        resp = self._login('newstudent')
        self.assertContains(resp, 'pending admin approval')
        self.assertNotIn('_auth_user_id', self.client.session)

        # Admin approves
        self.client.logout()
        self.client.force_login(self.admin)
        self.client.post(reverse('pending_users'), {'user_id': user.pk, 'action': 'approve'})
        user.profile.refresh_from_db()
        self.assertTrue(user.profile.is_approved)
        self.assertTrue(user.groups.filter(name='Student').exists())

        # Now login works and goes to the dashboard
        self.client.logout()
        resp = self._login('newstudent')
        self.assertRedirects(resp, reverse('dashboard'), fetch_redirect_response=False)

    def test_cannot_register_as_admin(self):
        resp = self.client.post(reverse('register'), {
            'username': 'hacker', 'email': 'h@example.com',
            'password1': 'StrongPass#123', 'password2': 'StrongPass#123', 'role': 'admin',
        })
        self.assertEqual(resp.status_code, 200)            # form re-shown with an error
        self.assertFalse(User.objects.filter(username='hacker').exists())

    # ---- the bug that used to crash the student dashboard -----------------
    def test_student_dashboard_with_enrolled_course_returns_200(self):
        self.client.force_login(self.student)
        resp = self.client.get(reverse('student_dashboard'))
        self.assertEqual(resp.status_code, 200)
        # the two links that used to raise NoReverseMatch
        self.assertContains(resp, reverse('student_attendance', args=[self.course.pk]))
        self.assertContains(resp, reverse('student_marks', args=[self.course.pk]))

    def test_student_attendance_and_marks_pages_load(self):
        Attendance.objects.create(enrollment=self.enrollment, date='2026-01-01', present=True)
        Marks.objects.create(enrollment=self.enrollment, exam_name='Mid', score=40, max_score=50)
        self.client.force_login(self.student)
        for name in ('student_attendance', 'student_marks'):
            resp = self.client.get(reverse(name, args=[self.course.pk]))
            self.assertEqual(resp.status_code, 200, name)

    def test_login_redirects_student_to_student_dashboard(self):
        resp = self._login('student1')
        self.assertRedirects(resp, reverse('dashboard'), fetch_redirect_response=False)
        resp = self.client.get(reverse('dashboard'))
        self.assertRedirects(resp, reverse('student_dashboard'), fetch_redirect_response=False)

    # ---- role-based access -------------------------------------------------
    def test_student_gets_403_on_teacher_and_admin_pages(self):
        self.client.force_login(self.student)
        for url in (reverse('teacher_dashboard'), reverse('admin_dashboard'),
                    reverse('teacher_attendance', args=[self.course.pk]),
                    reverse('pending_users'), reverse('users_csv')):
            self.assertEqual(self.client.get(url).status_code, 403, url)

    def test_teacher_cannot_open_another_teachers_course(self):
        self.client.force_login(self.teacher)
        for name in ('teacher_attendance', 'teacher_marks', 'teacher_course_students',
                     'teacher_attendance_csv'):
            resp = self.client.get(reverse(name, args=[self.course2.pk]))
            self.assertEqual(resp.status_code, 403, name)

    def test_student_cannot_see_other_students_data(self):
        other = self._make_user('student2', 'student')
        other_enrollment = Enrollment.objects.create(student=other, course=self.course2)
        Marks.objects.create(enrollment=other_enrollment, exam_name='Final', score=99, max_score=100)
        self.client.force_login(self.student)
        # student1 is not enrolled in course2: the view answers 404, so nothing leaks
        resp = self.client.get(reverse('student_marks', args=[self.course2.pk]))
        self.assertEqual(resp.status_code, 404)
        resp = self.client.get(reverse('student_attendance', args=[self.course2.pk]))
        self.assertEqual(resp.status_code, 404)

    # ---- lockout -----------------------------------------------------------
    def test_lockout_after_five_failed_logins(self):
        for _ in range(5):
            self._login('student1', 'wrong-password')
        self.assertTrue(LoginAttempt.objects.filter(username='student1',
                                                    locked_until__isnull=False).exists())
        # Even the CORRECT password is refused while locked
        resp = self._login('student1', 'StrongPass#123')
        self.assertContains(resp, 'Too many failed attempts')
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_lockout_message_is_same_for_unknown_username(self):
        for _ in range(5):
            self._login('does-not-exist', 'whatever')
        resp = self._login('does-not-exist', 'whatever')
        self.assertContains(resp, 'Too many failed attempts')

    def test_admin_can_unlock_account(self):
        for _ in range(5):
            self._login('student1', 'wrong-password')
        attempt = LoginAttempt.objects.filter(username='student1').first()
        self.client.force_login(self.admin)
        self.client.post(reverse('unlock_account', args=[attempt.pk]))
        self.assertFalse(LoginAttempt.objects.filter(username='student1').exists())

    # ---- exports and report card --------------------------------------------
    def test_teacher_can_export_own_course_attendance(self):
        Attendance.objects.create(enrollment=self.enrollment, date='2026-01-01', present=True)
        self.client.force_login(self.teacher)
        resp = self.client.get(reverse('teacher_attendance_csv', args=[self.course.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp['Content-Type'], 'text/csv')
        self.assertIn('student1', resp.content.decode())

    def test_student_report_card_loads(self):
        Marks.objects.create(enrollment=self.enrollment, exam_name='Mid', score=40, max_score=50)
        self.client.force_login(self.student)
        resp = self.client.get(reverse('report_card'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'PY101')

    def test_teacher_cannot_open_report_card(self):
        self.client.force_login(self.teacher)
        self.assertEqual(self.client.get(reverse('report_card')).status_code, 403)

    def test_csv_injection_is_neutralised(self):
        self.assertEqual(csv_safe('=SUM(A1:A9)'), "'=SUM(A1:A9)")
        self.assertEqual(csv_safe('+1'), "'+1")
        self.assertEqual(csv_safe('-1'), "'-1")
        self.assertEqual(csv_safe('@cmd'), "'@cmd")
        self.assertEqual(csv_safe('Alice'), 'Alice')

    def test_admin_users_csv_neutralises_formula_usernames(self):
        User.objects.create_user('=evil()', 'evil@example.com', 'StrongPass#123')
        self.client.force_login(self.admin)
        body = self.client.get(reverse('users_csv')).content.decode()
        self.assertIn("'=evil()", body)

    # ---- password reset -------------------------------------------------------
    def test_password_reset_pages_load(self):
        self.assertEqual(self.client.get(reverse('password_reset')).status_code, 200)
        self.assertEqual(self.client.get(reverse('password_reset_done')).status_code, 200)
        resp = self.client.post(reverse('password_reset'), {'email': 'student1@example.com'})
        self.assertRedirects(resp, reverse('password_reset_done'))
