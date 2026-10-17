from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User, Group
from .models import Profile, Course, Enrollment, Marks


class RegistrationForm(UserCreationForm):
    """
    Registration form that collects username, email, password, and role.
    Inherits UserCreationForm so Django's password validators run automatically.
    """
    email = forms.EmailField(required=True)

    # Only show Student and Teacher — never Admin
    role = forms.ChoiceField(choices=[
        ('student', 'Student'),
        ('teacher', 'Teacher'),
    ])

    class Meta:
        model = User
        # Fields rendered in this order on the template
        fields = ['username', 'email', 'password1', 'password2', 'role']

    def clean_email(self):
        """Reject duplicate emails so each user has a unique email address."""
        email = self.cleaned_data.get('email')
        if User.objects.filter(email=email).exists():
            raise forms.ValidationError("This email address is already registered.")
        return email

    def clean_role(self):
        """
        Server-side guard: even if someone edits the HTML to add an 'admin'
        option, this method will reject any value that isn't student/teacher.
        """
        role = self.cleaned_data.get('role')
        if role not in ('student', 'teacher'):
            raise forms.ValidationError("Invalid role selected.")
        return role

    def save(self, commit=True):
        """
        Save the User first (via parent), then create the linked Profile.
        is_staff and is_superuser are never set here — only createsuperuser does that.
        """
        user = super().save(commit=False)
        user.email = self.cleaned_data['email']
        # Explicitly ensure no privilege escalation from the form
        user.is_staff = False
        user.is_superuser = False
        if commit:
            user.save()
            # Create Profile with is_approved=False; admin must approve later
            Profile.objects.create(
                user=user,
                role=self.cleaned_data['role'],
                is_approved=False,
            )
        return user


# ---------------------------------------------------------------------------
# Admin: Course management
# ---------------------------------------------------------------------------
class CourseForm(forms.ModelForm):
    """
    Form for creating/editing a Course.
    The teacher queryset is restricted to approved users in the Teacher group.
    """
    class Meta:
        model = Course
        fields = ['name', 'code', 'teacher']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Only show approved teachers in the dropdown
        teacher_group = Group.objects.get(name='Teacher')
        self.fields['teacher'].queryset = User.objects.filter(
            groups=teacher_group,
            profile__is_approved=True,
        )
        self.fields['teacher'].required = False  # teacher can be assigned later
        # Add Bootstrap styling
        for field in self.fields.values():
            field.widget.attrs['class'] = 'form-control'


class EnrollmentForm(forms.ModelForm):
    """
    Form for enrolling a student in a course.
    Only approved students appear in the dropdown.
    """
    class Meta:
        model = Enrollment
        fields = ['student', 'course']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        student_group = Group.objects.get(name='Student')
        # Only approved students can be enrolled
        self.fields['student'].queryset = User.objects.filter(
            groups=student_group,
            profile__is_approved=True,
        )
        for field in self.fields.values():
            field.widget.attrs['class'] = 'form-control'


# ---------------------------------------------------------------------------
# Teacher: Attendance and Marks
# ---------------------------------------------------------------------------
class AttendanceDateForm(forms.Form):
    """
    Simple form to pick a date for marking attendance.
    The teacher selects a date; the view then loads all enrolled students.
    """
    date = forms.DateField(
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'})
    )


class MarksUploadForm(forms.Form):
    """
    Form to enter an exam name and max score before showing the per-student
    score inputs. The actual per-student scores are handled in the view.
    """
    exam_name = forms.CharField(
        max_length=100,
        widget=forms.TextInput(attrs={'class': 'form-control',
                                      'placeholder': 'e.g. Midterm Exam'})
    )
    max_score = forms.FloatField(
        initial=100,
        min_value=1,
        widget=forms.NumberInput(attrs={'class': 'form-control'})
    )


# ---------------------------------------------------------------------------
# Phase 4: Notice Board
# ---------------------------------------------------------------------------
class NoticeForm(forms.Form):
    """
    Form for posting a notice.
    The audience choices are restricted based on who is posting:
    - Admin can post to 'all', 'students', or 'teachers'.
    - Teachers can only post to 'students'.
    The restriction is enforced in __init__ (server-side), not just in the template.
    """
    title = forms.CharField(
        max_length=100,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Notice title (max 100 characters)',
        })
    )
    message = forms.CharField(
        max_length=1000,
        widget=forms.Textarea(attrs={
            'class': 'form-control',
            'rows': 4,
            'placeholder': 'Notice message (max 1000 characters)',
        })
    )
    audience = forms.ChoiceField(
        widget=forms.Select(attrs={'class': 'form-control'})
    )

    def __init__(self, *args, **kwargs):
        # Pop the user kwarg before calling super() — forms.Form doesn't know about it
        user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)

        # Restrict audience choices based on the poster's role
        if user and user.is_superuser:
            # Admin can target any audience
            self.fields['audience'].choices = [
                ('all',      'Everyone'),
                ('students', 'Students only'),
                ('teachers', 'Teachers only'),
            ]
        else:
            # Teachers can only post to students
            self.fields['audience'].choices = [
                ('students', 'Students only'),
            ]
