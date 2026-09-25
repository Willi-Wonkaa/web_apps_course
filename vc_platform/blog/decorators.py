from functools import wraps
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from blog.services.roles import is_manager


def manager_required(view_func):
    """ACL проверяется на бэкенде (access-control.md п.4.1): только активный менеджер."""
    @wraps(view_func)
    @login_required
    def _wrapped(request, *args, **kwargs):
        if not is_manager(request.user):
            raise PermissionDenied('Доступно только менеджерам.')
        return view_func(request, *args, **kwargs)
    return _wrapped
