from blog.services.roles import is_manager, is_team_leader, role_label


def role_flags(request):
    user = getattr(request, 'user', None)
    authenticated = bool(user and user.is_authenticated)
    return {
        'is_manager': is_manager(user) if authenticated else False,
        'is_team_leader': is_team_leader(user) if authenticated else False,
        'role_label': role_label(user) if authenticated else None,
    }
