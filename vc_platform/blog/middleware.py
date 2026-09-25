"""Middleware специфичные для TG Mini App (architecture.md §2.1, §3)."""


class TelegramWebAppFrameMiddleware:
    """Разрешает встраивание сайта в `<iframe>` только для сессий, вошедших через
    Mini App (`views.webapp_auth` ставит `request.session['is_webapp_session'] = True`).
    Обычный веб-вход по логину/паролю по-прежнему защищён `XFrameOptionsMiddleware`
    (DENY) — этот middleware должен идти позже него в `MIDDLEWARE`, чтобы иметь
    возможность переопределить уже выставленный заголовок.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if getattr(request, 'session', None) and request.session.get('is_webapp_session'):
            response['X-Frame-Options'] = 'ALLOWALL'
        return response
