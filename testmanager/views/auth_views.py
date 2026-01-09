"""
Authentication Views - Login, Registration, Password Reset

Separate from admin authentication - these are for the main application.
"""

from django.shortcuts import render, redirect
from django.contrib.auth import login, authenticate
from django.contrib.auth.models import User
from django.contrib import messages
from django.contrib.auth.views import PasswordResetView, PasswordResetDoneView, PasswordResetConfirmView, PasswordResetCompleteView
from django.urls import reverse_lazy, reverse
from django.core.exceptions import ValidationError
from django.views.decorators.csrf import csrf_protect
from django.utils.decorators import method_decorator
from django.views import View
from django.forms import ModelForm, CharField, EmailField, PasswordInput
from django import forms
from django.http import HttpResponseRedirect


class UserRegistrationForm(ModelForm):
    """Form for user registration with strict validation."""
    password = CharField(
        widget=PasswordInput(attrs={'class': 'form-control'}),
        required=True,
        label="Password",
        min_length=8,
        help_text="Password must be at least 8 characters long."
    )
    confirm_password = CharField(
        widget=PasswordInput(attrs={'class': 'form-control'}),
        required=True,
        label="Confirm Password"
    )
    email = EmailField(
        required=True,
        label="Email Address",
        help_text="Must be a @tvsmotor.com email address."
    )
    
    class Meta:
        model = User
        fields = ['username', 'email', 'password']
        widgets = {
            'username': forms.TextInput(attrs={'class': 'form-control'}),
            'email': forms.EmailInput(attrs={'class': 'form-control'}),
        }
        labels = {
            'username': 'Username',
            'email': 'Email Address',
        }
    
    def clean_email(self):
        """Validate email must end with @tvsmotor.com"""
        email = self.cleaned_data.get('email')
        if email and not email.endswith('@tvsmotor.com'):
            raise ValidationError("Email must be a @tvsmotor.com address.")
        return email
    
    def clean(self):
        """Validate password match and other rules."""
        cleaned_data = super().clean()
        password = cleaned_data.get('password')
        confirm_password = cleaned_data.get('confirm_password')
        
        if password and confirm_password and password != confirm_password:
            raise ValidationError("Passwords do not match.")
        
        return cleaned_data


@method_decorator(csrf_protect, name='dispatch')
class CustomLoginView(View):
    """Custom login view with modern UI."""
    template_name = 'testmanager/login.html'
    
    def get(self, request):
        # Redirect if already authenticated
        if request.user.is_authenticated:
            return redirect('home')
        return render(request, self.template_name)
    
    def post(self, request):
        username = request.POST.get('username')
        password = request.POST.get('password')
        
        if username and password:
            user = authenticate(request, username=username, password=password)
            if user is not None:
                # Check if user is active
                if not user.is_active:
                    messages.error(request, "This account is inactive.")
                    return render(request, self.template_name)
                
                # Login the user
                login(request, user)
                
                # Set session to expire on browser close (0 = session cookie)
                # Do this AFTER login to ensure session is properly initialized
                request.session.set_expiry(0)
                
                # Ensure session is saved before redirect
                request.session.modified = True
                
                # Redirect to home page using HttpResponseRedirect to ensure proper redirect
                return HttpResponseRedirect(reverse('home'))
            else:
                messages.error(request, "Invalid username or password.")
        else:
            messages.error(request, "Please provide both username and password.")
        
        return render(request, self.template_name)


def register_user(request):
    """User registration view with strict validation."""
    if request.method == 'POST':
        form = UserRegistrationForm(request.POST)
        if form.is_valid():
            # Create user
            user = form.save(commit=False)
            user.set_password(form.cleaned_data['password'])
            user.is_active = True
            user.save()
            
            messages.success(request, "User created successfully. Please login.")
            return redirect('login')
    else:
        form = UserRegistrationForm()
    
    return render(request, 'testmanager/register.html', {'form': form})


class CustomPasswordResetView(PasswordResetView):
    """Custom password reset view with @tvsmotor.com validation."""
    template_name = 'testmanager/password_reset.html'
    email_template_name = 'testmanager/password_reset_email.html'
    subject_template_name = 'testmanager/password_reset_subject.txt'
    success_url = reverse_lazy('password_reset_done')
    
    def form_valid(self, form):
        """Validate email domain and send reset email."""
        email = form.cleaned_data.get('email', '')
        
        # Validate email format
        if not email.endswith('@tvsmotor.com'):
            messages.error(self.request, "Email must be a @tvsmotor.com address.")
            return self.form_invalid(form)
        
        # Call parent to send email (always shows success message for security)
        messages.success(self.request, "If an account exists with that email, password reset instructions have been sent.")
        return super().form_valid(form)


class CustomPasswordResetDoneView(PasswordResetDoneView):
    """Password reset done view."""
    template_name = 'testmanager/password_reset_done.html'


class CustomPasswordResetConfirmView(PasswordResetConfirmView):
    """Password reset confirm view."""
    template_name = 'testmanager/password_reset_confirm.html'
    success_url = reverse_lazy('password_reset_complete')


class CustomPasswordResetCompleteView(PasswordResetCompleteView):
    """Password reset complete view."""
    template_name = 'testmanager/password_reset_complete.html'

