"""Проверка Telegram WebApp `initData` (Mini App, db.md правило 8, architecture.md §2.1).

Telegram подписывает данные пользователя HMAC-ключом, полученным из токена бота —
это заменяет пароль для входа: раз подпись верна, значит запрос действительно пришёл
из Telegram WebView для конкретного `telegram_id`. Алгоритм — из официальной документации
Telegram (https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app).
"""
import hashlib
import hmac
import json
from urllib.parse import parse_qsl

from django.conf import settings

MAX_AUTH_AGE_SECONDS = 86400  # initData считается свежим не дольше суток


def _secret_key(bot_token: str) -> bytes:
    return hmac.new(b'WebAppData', bot_token.encode(), hashlib.sha256).digest()


def validate_init_data(init_data: str, bot_token: str = None) -> dict | None:
    """Возвращает распарсенные поля `initData` (включая `user` как dict), если подпись
    верна и данные не устарели; иначе None. Не бросает исключений на невалидном вводе —
    вызывающий код должен трактовать None как «не авторизован»."""
    bot_token = bot_token or settings.TG_BOT_TOKEN
    if not init_data or not bot_token:
        return None

    try:
        pairs = dict(parse_qsl(init_data, strict_parsing=True))
    except ValueError:
        return None

    received_hash = pairs.pop('hash', None)
    if not received_hash:
        return None

    data_check_string = '\n'.join(f'{k}={v}' for k, v in sorted(pairs.items()))
    secret_key = _secret_key(bot_token)
    computed_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(computed_hash, received_hash):
        return None

    auth_date = pairs.get('auth_date')
    if auth_date:
        import time
        if time.time() - int(auth_date) > MAX_AUTH_AGE_SECONDS:
            return None

    if 'user' in pairs:
        try:
            pairs['user'] = json.loads(pairs['user'])
        except (json.JSONDecodeError, TypeError):
            pairs['user'] = None

    return pairs


def validate_login_widget(data: dict, bot_token: str = None) -> dict | None:
    """Проверка данных Telegram Login Widget (браузерный вход вне Mini App).

    Отличается от `validate_init_data` алгоритмом: у Login Widget секретный ключ —
    это `sha256(bot_token)`, а поля приходят готовым dict (id/first_name/username/
    photo_url/auth_date/hash), а не одной urlencoded-строкой. Алгоритм — из офиц.
    документации Telegram (https://core.telegram.org/widgets/login#checking-authorization).

    Возвращает dict полей (с int `id`) при верной подписи и свежем `auth_date`,
    иначе None.
    """
    bot_token = bot_token or settings.TG_BOT_TOKEN
    if not data or not bot_token:
        return None

    received_hash = data.get('hash')
    if not received_hash:
        return None

    pairs = {k: v for k, v in data.items() if k != 'hash'}
    data_check_string = '\n'.join(f'{k}={pairs[k]}' for k in sorted(pairs))
    secret_key = hashlib.sha256(bot_token.encode()).digest()
    computed_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(computed_hash, received_hash):
        return None

    auth_date = pairs.get('auth_date')
    if auth_date:
        import time
        if time.time() - int(auth_date) > MAX_AUTH_AGE_SECONDS:
            return None

    return pairs
