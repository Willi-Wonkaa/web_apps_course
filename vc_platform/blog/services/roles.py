"""Резолвинг ролей и контекстных прав тим-лидера (access-control.md)."""
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from blog.models import Application, EventStaff, Manager, Participation, TeamLeader, User


def is_manager(user: User) -> bool:
    if not user or not user.is_authenticated:
        return False
    return user.manager_roles.filter(revoked_at__isnull=True).exists()


def is_team_leader(user: User) -> bool:
    if not user or not user.is_authenticated:
        return False
    return user.team_leader_roles.filter(revoked_at__isnull=True).exists()


def is_team_leader_of_event(user: User, event_id: int) -> bool:
    """Является ли user активным тим-лидером конкретного мероприятия."""
    if not user or not user.is_authenticated:
        return False
    return EventStaff.objects.filter(
        event_id=event_id, user=user, staff_role='team_lead'
    ).exists()


def can_process_applications(user: User, event_id: int) -> bool:
    """Менеджер — на всех меро; тим-лидер — только на своих (правило 3 db.md)."""
    return is_manager(user) or is_team_leader_of_event(user, event_id)


def can_manage_event(user: User) -> bool:
    """CRUD мероприятий, назначение персонала — только менеджер."""
    return is_manager(user)


def has_extended_access_to_profile(viewer: User, target: User) -> bool:
    """Расширенный + внутренний профиль чужого волонтёра (access-control.md, правило 5):

    владелец или менеджер — всегда; тим-лидер — только пока одновременно:
    - он team_lead на мероприятии, и
    - мероприятие ещё не завершено (event_status != 'finished'), и
    - целевой волонтёр подал заявку на это меро ИЛИ участвует в нём.
    """
    if not viewer or not viewer.is_authenticated:
        return False
    if viewer.id == target.id or is_manager(viewer):
        return True
    if not is_team_leader(viewer):
        return False

    tl_event_ids = EventStaff.objects.filter(
        user=viewer, staff_role='team_lead'
    ).exclude(event__event_status='finished').values_list('event_id', flat=True)

    if not tl_event_ids:
        return False

    has_application = Application.objects.filter(
        user=target, event_id__in=tl_event_ids
    ).exists()
    has_participation = Participation.objects.filter(
        user=target, event_day__event_id__in=tl_event_ids
    ).exists()

    return has_application or has_participation


def account_is_complete(user: User) -> bool:
    """«Заполненный аккаунт» — минимум, при котором пользователь может подать заявку:
    известно ФИО (хотя бы имя) и есть ссылка на Telegram (username или реальный
    telegram_id из бота). Авторизация в боте не обязательна — заявка может прийти из
    Excel-выгрузки, там достаточно ФИО + Telegram. Пользователи с веб-входом
    (login/пароль, служебный telegram_id `web:*`) без TG-username сюда не попадают —
    им заявки подавать не нужно."""
    if not user or not getattr(user, 'is_authenticated', False):
        return False
    has_name = bool((user.first_name or '').strip())
    # Реальный telegram_id пользователя — положительное целое (служебные web:*-id и
    # отрицательные id каналов/групп не считаем «привязкой к Telegram»).
    real_tg_id = bool(user.telegram_id) and user.telegram_id.isdigit()
    has_tg = bool((user.telegram_username or '').strip()) or real_tg_id
    return has_name and has_tg


def role_label(user: User) -> str:
    if is_manager(user):
        return 'Менеджер'
    if is_team_leader(user):
        return 'Тим-лидер'
    return 'Волонтёр'


# ---------------------------------------------------------------------------
# Назначение/снятие ролей (настройки, только менеджер — см. settings_view)
# ---------------------------------------------------------------------------

ROLE_MODELS = {'team_lead': TeamLeader, 'manager': Manager}


def users_without_role(kind: str):
    """Волонтёры, у которых сейчас НЕТ активной роли `kind` ('team_lead'/'manager') —
    кандидаты для назначения, отсортированы по алфавиту (фамилия, имя)."""
    model = ROLE_MODELS[kind]
    active_user_ids = model.objects.filter(revoked_at__isnull=True).values('user_id')
    return User.objects.exclude(id__in=active_user_ids).order_by('last_name', 'first_name')


def users_with_role(kind: str):
    """Пользователи с активной ролью `kind` — кандидаты для снятия роли."""
    model = ROLE_MODELS[kind]
    active_user_ids = model.objects.filter(revoked_at__isnull=True).values('user_id')
    return User.objects.filter(id__in=active_user_ids).order_by('last_name', 'first_name')


@transaction.atomic
def assign_role(kind: str, users, assigned_by: User):
    """Выдаёт роль `kind` каждому из `users`, у кого её ещё нет. Возвращает список
    реально затронутых пользователей (уже имевших роль — пропускаются молча).

    Менеджеру автоматически выдаётся и роль тим-лидера (если её ещё нет) — чтобы
    менеджера можно было назначать тим-лидером на мероприятия наравне с обычными
    тим-лидерами (правило: у менеджера всегда есть профиль ТЛ)."""
    model = ROLE_MODELS[kind]
    already_active = set(
        model.objects.filter(revoked_at__isnull=True, user__in=users).values_list('user_id', flat=True)
    )
    affected = []
    for user in users:
        if user.id in already_active:
            continue
        model.objects.create(user=user, assigned_by=assigned_by)
        affected.append(user)
        if kind == 'manager' and not TeamLeader.objects.filter(user=user, revoked_at__isnull=True).exists():
            TeamLeader.objects.create(user=user, assigned_by=assigned_by)
    return affected


@transaction.atomic
def revoke_role(kind: str, users):
    """Снимает активную роль `kind` у каждого из `users`. Возвращает список
    реально затронутых пользователей."""
    model = ROLE_MODELS[kind]
    now = timezone.now()
    updated_ids = list(
        model.objects.filter(revoked_at__isnull=True, user__in=users).values_list('user_id', flat=True)
    )
    model.objects.filter(revoked_at__isnull=True, user__in=users).update(revoked_at=now)
    return [u for u in users if u.id in updated_ids]
