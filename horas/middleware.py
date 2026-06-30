from django.conf import settings
from django.shortcuts import redirect
from django.urls import reverse

from .models import UserProfile


class ScriptNamePrefixMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        script_name = settings.FORCE_SCRIPT_NAME
        if script_name:
            prefix = script_name.rstrip('/')
            if request.path_info == prefix:
                request.path_info = '/'
            elif request.path_info.startswith(f'{prefix}/'):
                request.path_info = request.path_info[len(prefix):]

        return self.get_response(request)


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
        static_url = settings.STATIC_URL
        script_name = settings.FORCE_SCRIPT_NAME or ''
        if script_name and static_url.startswith(f'{script_name}/'):
            static_url = static_url[len(script_name):]

        allowed_prefixes = (
            reverse('password_change_required'),
            reverse('logout'),
            reverse('login'),
            static_url,
            '/admin/',
        )
        if any(path.startswith(prefix) for prefix in allowed_prefixes):
            return False

        profile, _ = UserProfile.objects.get_or_create(user=user)
        return profile.must_change_password
