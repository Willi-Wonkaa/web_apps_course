"""Вспомогательные функции браузерного входа через Telegram (раздел 2b).

Два пути:
- Telegram Login Widget — HMAC-проверка в `blog.services.telegram_auth.validate_login_widget`,
  логин прямо на странице входа.
- Deep-link «войти через бота»: бот создаёт одноразовый `TelegramAuthToken` (TTL 5 мин)
  и присылает ссылку `/auth/tg/<token>/`; переход по ней логинит в веб-сессию.
"""
import datetime

from django.utils import timezone

from blog.models import TelegramAuthToken, User

# Время жизни одноразовой ссылки входа через бота.
AUTH_TOKEN_TTL = datetime.timedelta(minutes=5)


def issue_auth_token(user: User) -> TelegramAuthToken:
    """Создаёт одноразовый токен входа для пользователя (вызывается ботом при
    deep-link `?start=weblogin`). Старые неиспользованные токены того же
    пользователя гасим, чтобы жила только последняя ссылка."""
    TelegramAuthToken.objects.filter(user=user, used_at__isnull=True).update(
        used_at=timezone.now()
    )
    return TelegramAuthToken.objects.create(
        user=user, expires_at=timezone.now() + AUTH_TOKEN_TTL
    )


def consume_auth_token(token: str):
    """Возвращает пользователя по валидному одноразовому токену и помечает токен
    использованным, либо None, если токен неизвестен/просрочен/уже использован."""
    obj = TelegramAuthToken.objects.filter(token=token).select_related('user').first()
    if obj is None or not obj.is_valid():
        return None
    obj.used_at = timezone.now()
    obj.save(update_fields=['used_at'])
    return obj.user
