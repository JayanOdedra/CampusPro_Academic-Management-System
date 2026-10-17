# CampusPro - Interview Notes

## 1-minute explanation
"CampusPro is a role-based academic management system built with Django. Admins, teachers and
students log in and see different dashboards. Registration is approval-based, so nobody can become
an admin from the website. Core data like users, courses, attendance and marks is in SQLite using the
Django ORM. Audit logs and notices are stored in MongoDB, and the app keeps working even if MongoDB is down.
I added security features such as role-based access control, a login lockout after five wrong passwords,
CSRF protection and CSV-injection protection, and Chart.js dashboards for attendance and marks."

## 15 likely questions (about THIS code)
1. **What is Django MVT?** Model = database tables (`models.py`), View = logic (`views.py`), Template = HTML.
   The URL file maps a URL to a view.
2. **What is the ORM and a migration?** The ORM lets me use Python classes instead of SQL.
   `makemigrations` writes the change as a file, `migrate` applies it to the database.
3. **How did you do roles?** Admin = Django superuser. Teacher and Student are Django Groups, plus a `Profile`
   with `role` and `is_approved`. A `role_required` decorator checks the role before a view runs.
4. **Why approval-based registration?** To stop privilege escalation. The form only offers Student/Teacher, the
   server re-checks the role, and new users stay `is_approved=False` until the admin approves.
5. **What is privilege escalation?** A user gaining more rights than they should, for example registering as admin by
   editing the HTML. I block it on the server, not only in the browser.
6. **What is CSRF and how is it handled?** An attack that makes a logged-in user's browser send a request they did not
   intend. Every POST form has `{% csrf_token %}`, and approve, delete and logout use POST only.
7. **How does a student only see their own data?** Views use `request.user`, never an id from the URL. If a student opens
   a course they are not enrolled in, they get a 404.
8. **How do you stop a teacher opening another teacher's course?** `_teacher_owns_course` compares the course teacher with
   `request.user`; otherwise the view returns the 403 page.
9. **How does the login lockout work?** `LoginAttempt` stores failures per username and IP. After 5 failures the username is
   locked for 5 minutes, even a correct password is refused, and the admin can unlock it. The same message is shown for
   unknown usernames, so attackers cannot find which usernames exist.
10. **Why two databases?** SQLite for relational data that needs foreign keys and constraints. MongoDB for write-heavy,
    flexible documents (audit logs, notices). A logging problem cannot damage core data.
11. **What if MongoDB is down?** Every Mongo call is in try/except with a 3 second timeout, so the app continues and only
    skips the logging.
12. **What is XSS and how do you prevent it?** Injecting JavaScript into a page. Django templates escape output
    automatically, I never use `|safe` on user data, and chart data goes through `json_script`.
13. **What is NoSQL injection?** Putting query operators in user input. I build MongoDB filters only from validated values
    (a whitelist of actions, `re.escape` for search text, `ObjectId.is_valid` for ids).
14. **What is CSV injection?** A cell starting with `=`, `+`, `-` or `@` can run as a formula in Excel. `csv_safe()` adds a
    leading quote in every export.
15. **How do you calculate attendance % and the grade?** Attendance = present records / total records x 100
    (`utils.attendance_percentage`). The grade comes from the overall marks percentage: A >= 80, B >= 65, C >= 50, D >= 40,
    otherwise Fail. `annotate`/`Sum` calculate totals inside the database.

## A bug I found and fixed (good story)
The student dashboard crashed with `NoReverseMatch` once a student was enrolled in a course. The template used
`{% url 'student_attendance' %}` and `{% url 'student_marks' %}`, but the two routes were never added to `urls.py`.
Lesson: the view, the URL pattern and the template's `{% url %}` name must all match. I also found that chart data was
JSON-encoded twice (`json.dumps` plus `json_script`), so the charts received a string instead of a list.

## Limitations and future improvements
- Lockout is per username, so someone could deliberately lock another person's account; a CAPTCHA or per-IP limits would help.
- Add a REST API (Django REST Framework), PostgreSQL, Docker, CI and deployment.
- PDF report card uses the browser's Print to PDF; a PDF library could generate it on the server.
- E-mails are printed in the terminal until real SMTP settings are added to `.env`.
