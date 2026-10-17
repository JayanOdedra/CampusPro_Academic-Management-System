from django.db import models
from django.contrib.auth.models import User
from django.db.models.signals import post_migrate
from django.dispatch import receiver


class Profile(models.Model):
    """
    Extends Django's built-in User with a role and approval status.
    OneToOneField means every User has at most one Profile.
    """
    ROLE_CHOICES = [
        ('student', 'Student'),
        ('teacher', 'Teacher'),
        # 'admin' is intentionally excluded — admin = Django superuser only
    ]

    # Link to Django's User; deleting the User also deletes the Profile
    user = models.OneToOneField(User, on_delete=models.CASCADE)

    # Role chosen at registration
    role = models.CharField(max_length=10, choices=ROLE_CHOICES)

    # Admin must explicitly approve each new account before they can log in
    is_approved = models.BooleanField(default=False)

    # Automatically set when the Profile is first created
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user.username} ({self.role})"


# ---------------------------------------------------------------------------
# Signal: runs after every `migrate` command.
# Creates the "Teacher" and "Student" groups if they don't already exist.
# Using get_or_create means it's safe to run multiple times.
# ---------------------------------------------------------------------------
@receiver(post_migrate)
def create_groups(sender, **kwargs):
    # Import here to avoid AppRegistryNotReady errors at module load time
    from django.contrib.auth.models import Group
    Group.objects.get_or_create(name='Teacher')
    Group.objects.get_or_create(name='Student')


# ---------------------------------------------------------------------------
# Course
# ---------------------------------------------------------------------------
class Course(models.Model):
    """
    A course has a unique code and is optionally assigned to one teacher.
    limit_choices_to restricts the teacher dropdown to users in the Teacher group.
    SET_NULL means if the teacher is deleted, the course remains but has no teacher.
    """
    name = models.CharField(max_length=200)
    # Course code must be unique across the whole table (e.g. CS101)
    code = models.CharField(max_length=20, unique=True)
    # ForeignKey: many courses can belong to one teacher
    # limit_choices_to filters the queryset to Teacher-group members only
    teacher = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='courses_taught',
        limit_choices_to={'groups__name': 'Teacher'},
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.code} — {self.name}"


# ---------------------------------------------------------------------------
# Enrollment
# ---------------------------------------------------------------------------
class Enrollment(models.Model):
    """
    Links a student to a course.
    unique_together prevents the same student from being enrolled twice.
    """
    # Only users in the Student group should be enrolled
    student = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='enrollments',
        limit_choices_to={'groups__name': 'Student'},
    )
    course = models.ForeignKey(
        Course,
        on_delete=models.CASCADE,
        related_name='enrollments',
    )
    enrolled_on = models.DateField(auto_now_add=True)

    # A student can only be enrolled once per course
    class Meta:
        unique_together = ('student', 'course')

    def __str__(self):
        return f"{self.student.username} → {self.course.code}"


# ---------------------------------------------------------------------------
# Attendance
# ---------------------------------------------------------------------------
class Attendance(models.Model):
    """
    Records whether a student was present on a specific date for a course.
    Tied to Enrollment (not directly to student+course) so we always know
    the student is actually enrolled before marking attendance.
    unique_together prevents marking the same student twice on the same day.
    """
    enrollment = models.ForeignKey(
        Enrollment,
        on_delete=models.CASCADE,
        related_name='attendances',
    )
    date = models.DateField()
    # True = Present, False = Absent
    present = models.BooleanField(default=False)

    # Same enrollment cannot have two attendance records on the same date
    class Meta:
        unique_together = ('enrollment', 'date')

    def __str__(self):
        status = 'Present' if self.present else 'Absent'
        return f"{self.enrollment} on {self.date}: {status}"


# ---------------------------------------------------------------------------
# Marks
# ---------------------------------------------------------------------------
class Marks(models.Model):
    """
    Stores a student's score for a named exam within a course.
    Validation ensures score is between 0 and max_score.
    """
    enrollment = models.ForeignKey(
        Enrollment,
        on_delete=models.CASCADE,
        related_name='marks',
    )
    exam_name = models.CharField(max_length=100)  # e.g. "Midterm", "Final"
    score = models.FloatField()
    max_score = models.FloatField(default=100)

    def clean(self):
        """
        Django calls clean() during full_clean() / form validation.
        We enforce 0 <= score <= max_score here at the model level.
        """
        from django.core.exceptions import ValidationError
        if self.score < 0 or self.score > self.max_score:
            raise ValidationError(
                f"Score must be between 0 and {self.max_score}."
            )

    def __str__(self):
        return f"{self.enrollment} | {self.exam_name}: {self.score}/{self.max_score}"


# ---------------------------------------------------------------------------
# Phase 5: Login Attempt Tracking (stored in SQLite)
# ---------------------------------------------------------------------------
class LoginAttempt(models.Model):
    """
    Tracks failed login attempts per username AND per IP address.
    When failed_count reaches MAX_ATTEMPTS, locked_until is set to
    now + LOCK_DURATION. The login view checks this before authenticating.

    Stored in SQLite (not MongoDB) because:
    - It needs transactional consistency (read-then-write must be atomic).
    - It is tightly coupled to the auth flow, not an audit/log concern.
    """
    MAX_ATTEMPTS  = 5                          # lock after this many failures
    LOCK_MINUTES  = 5                          # lock duration in minutes

    # We track by username so a single account can be locked regardless of IP
    username    = models.CharField(max_length=150, db_index=True)
    # We also track by IP so a single IP cannot brute-force many accounts
    ip_address  = models.GenericIPAddressField(db_index=True)
    failed_count = models.PositiveIntegerField(default=0)
    # NULL means not locked; a datetime means locked until that time
    locked_until = models.DateTimeField(null=True, blank=True)
    last_attempt = models.DateTimeField(auto_now=True)  # updated on every attempt

    class Meta:
        # One row per (username, ip) pair
        unique_together = ('username', 'ip_address')

    def __str__(self):
        return f"{self.username} from {self.ip_address} ({self.failed_count} fails)"
