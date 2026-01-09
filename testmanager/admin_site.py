from django.contrib.admin import AdminSite
from django.shortcuts import redirect
from django.urls import reverse
from django.template.response import TemplateResponse
from django.contrib.auth.models import User


class CustomAdminSite(AdminSite):
    """
    Custom AdminSite for test manager.
    Uses default Django admin UI with custom backend logic only.
    """
    
    def login(self, request, extra_context=None):
        # Let Django handle authentication first
        response = super().login(request, extra_context)

        # Only redirect AFTER successful POST login
        if request.method == "POST" and request.user.is_authenticated:
            # Always redirect to home page after login
            return redirect(reverse("home"))

        return response


custom_admin_site = CustomAdminSite(name="custom_admin")
