"""
academics/utils.py
------------------
Reusable calculation helpers shared across views.
Centralising logic here means we never duplicate the same formula in multiple
views — if the grading thresholds change, we only edit one place.
"""

from django.db.models import Count, Sum


def attendance_percentage(enrollment):
    """
    Given a single Enrollment object, return the attendance percentage (float).
    Uses .count() on the related manager — Django issues one SQL query per call.
    Returns 0.0 if no attendance records exist yet (avoids ZeroDivisionError).
    """
    total = enrollment.attendances.count()          # all records for this enrollment
    if total == 0:
        return 0.0
    attended = enrollment.attendances.filter(present=True).count()
    return round((attended / total) * 100, 1)


def grade_from_percentage(pct):
    """
    Convert a numeric percentage to a letter grade.
    Returns 'N/A' when pct is None (no marks uploaded yet).

    Thresholds:
        A  >= 80
        B  >= 65
        C  >= 50
        D  >= 40
        Fail < 40
    """
    if pct is None:
        return 'N/A'
    if pct >= 80:
        return 'A'
    if pct >= 65:
        return 'B'
    if pct >= 50:
        return 'C'
    if pct >= 40:
        return 'D'
    return 'Fail'


def marks_summary(enrollment):
    """
    Given a single Enrollment, return (overall_pct, grade) across all exams.
    Uses aggregate Sum so Django computes the totals in a single SQL query
    instead of looping over every Marks row in Python.

    aggregate() collapses the entire queryset into one dict of computed values.
    """
    from .models import Marks  # local import avoids circular imports at module load
    result = Marks.objects.filter(enrollment=enrollment).aggregate(
        total_score=Sum('score'),
        total_max=Sum('max_score'),
    )
    total_score = result['total_score'] or 0
    total_max = result['total_max'] or 0
    if total_max == 0:
        return None, 'N/A'
    pct = round((total_score / total_max) * 100, 1)
    return pct, grade_from_percentage(pct)


def course_attendance_avg(course):
    """
    Return the average attendance percentage across ALL students in a course.
    We iterate over enrollments in Python because SQLite doesn't support
    conditional aggregation easily; for small class sizes this is fine.
    Returns 0.0 if no enrollments exist.
    """
    enrollments = course.enrollments.all()
    if not enrollments.exists():
        return 0.0
    total_pct = sum(attendance_percentage(e) for e in enrollments)
    return round(total_pct / enrollments.count(), 1)


def course_marks_avg(course):
    """
    Return the average marks percentage across ALL students in a course.
    Returns 0.0 if no marks exist.
    """
    from .models import Marks
    marks = Marks.objects.filter(enrollment__course=course)
    if not marks.exists():
        return 0.0
    # Sum all scores and all max_scores across every exam for every student
    result = marks.aggregate(total_score=Sum('score'), total_max=Sum('max_score'))
    total_score = result['total_score'] or 0
    total_max = result['total_max'] or 0
    if total_max == 0:
        return 0.0
    return round((total_score / total_max) * 100, 1)


def csv_safe(value):
    """
    Prevent CSV injection (also called formula injection).

    Spreadsheet applications like Excel and LibreOffice Calc treat cells
    that start with =, +, -, or @ as formulas. An attacker could store
    a value like '=HYPERLINK("http://evil.com","Click me")' in the database,
    and when a user opens the exported CSV, the formula executes.

    Fix: if the string value starts with one of those characters, prefix it
    with a single quote. The quote is visible in the raw CSV but most
    spreadsheet apps strip it and display the rest as plain text.

    Example:
        csv_safe('=SUM(A1:A10)')  →  "'=SUM(A1:A10)"
        csv_safe('Alice')         →  'Alice'   (unchanged)
    """
    value = str(value) if value is not None else ''
    # These characters trigger formula evaluation in spreadsheet apps
    if value.startswith(('=', '+', '-', '@')):
        return "'" + value   # prefix with a literal single quote
    return value
