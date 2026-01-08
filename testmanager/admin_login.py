from django.contrib.auth.views import LoginView
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.debug import sensitive_post_parameters


@method_decorator(sensitive_post_parameters(), name='dispatch')
@method_decorator(csrf_protect, name='dispatch')
@method_decorator(never_cache, name='dispatch')
class CustomAdminLoginView(LoginView):
    """
    Custom admin login view that redirects to home page after login
    """
    template_name = 'admin/login.html'
    redirect_authenticated_user = True
    
    def get_success_url(self):
        # Always redirect to home page after login
        return reverse('home')
    
    def form_valid(self, form):
        """Security check complete. Log the user in."""
        from django.contrib.auth import login
        user = form.get_user()
        login(self.request, user)
        # Redirect to home page
        return redirect(reverse('home'))
