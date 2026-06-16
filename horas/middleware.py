from django.conf import settings
from django.shortcuts import redirect
from django.urls import reverse

from .models import UserProfile


class RequiredPasswordChangeMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if self._must_redirect(request):
            return redirect('password_change_required')
        return self.get_response(request)

    def _must_redirect(self, request):
        user = getattr(request, 'user', None)
        if not user or not user.is_authenticated:
            return False

        path = request.path_info
        allowed_prefixes = (
            reverse('password_change_required'),
            reverse('logout'),
            reverse('login'),
            settings.STATIC_URL,
            '/admin/',
        )
        if any(path.startswith(prefix) for prefix in allowed_prefixes):
            return False

        profile, _ = UserProfile.objects.get_or_create(user=user)
        return profile.must_change_password
