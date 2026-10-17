from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    # Django's built-in admin site (for superuser model management)
    path('django-admin/', admin.site.urls),
    # All application URLs are defined in academics/urls.py
    path('', include('academics.urls')),
]
