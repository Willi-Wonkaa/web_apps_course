from django.contrib.auth.backends import BaseBackend
from blog.models import User


class WebLoginBackend(BaseBackend):
    """Аутентификация по login/password для Web Platform (см. architecture.md 2.1)."""

    def authenticate(self, request, login=None, password=None, **kwargs):
        if not login or not password:
            return None
        try:
            user = User.objects.get(login=login)
        except User.DoesNotExist:
            return None
        if user.has_usable_password() and user.check_password(password):
            return user
        return None

    def get_user(self, user_id):
        try:
            return User.objects.get(pk=user_id)
        except User.DoesNotExist:
            return None


class TelegramWebAppBackend(BaseBackend):
    """Вход в TG Mini App по проверенному Telegram `initData` (architecture.md §2.1) —
    без пароля. Сам `authenticate()` не вызывается напрямую (пользователь уже найден/создан
    в `views.webapp_auth` через `data_controller`); backend нужен только для `django.contrib.auth.login`,
    чтобы указать явный `backend=` и для последующего `get_user` по сессии."""

    def authenticate(self, request, **kwargs):
        return None

    def get_user(self, user_id):
        try:
            return User.objects.get(pk=user_id)
        except User.DoesNotExist:
            return None
