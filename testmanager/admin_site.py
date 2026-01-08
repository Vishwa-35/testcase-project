from django.contrib.admin import AdminSite
from django.shortcuts import redirect
from django.urls import reverse
from django.template.response import TemplateResponse
from django.contrib.auth.models import User


class CustomAdminSite(AdminSite):
    """
    Custom AdminSite that uses admin_page.html as the index template.
    
    This maintains Django admin backend logic while providing a custom UI.
    """
    site_header = "Test Case Management System"
    site_title = "Test Case Admin"
    index_title = "Administration"
    
    # Override index template to use custom admin_page.html
    index_template = "admin/admin_page.html"

    def login(self, request, extra_context=None):
        # Let Django handle authentication first
        response = super().login(request, extra_context)

        # Only redirect AFTER successful POST login
        if request.method == "POST" and request.user.is_authenticated:
            # Always redirect to home page after login
            return redirect(reverse("home"))

        return response
    
    def index(self, request, extra_context=None):
        """
        Override index to add users context for admin_page.html template.
        This maintains Django admin backend while adding custom context.
        """
        extra_context = extra_context or {}
        # Add users for user management table
        extra_context['users'] = User.objects.all().select_related('profile').order_by('-date_joined')
        return super().index(request, extra_context)
    
    def each_context(self, request):
        """
        Add custom CSS to all admin pages for professional UI styling.
        """
        context = super().each_context(request)
        context['admin_custom_css'] = 'testmanager/css/admin_custom.css'
        return context
    
    def get_urls(self):
        """
        Override to ensure custom CSS is available on all admin pages.
        """
        urls = super().get_urls()
        return urls


custom_admin_site = CustomAdminSite(name="custom_admin")
