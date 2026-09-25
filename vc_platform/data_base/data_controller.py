from typing import Optional, List, Dict, Any
from django.db.models import Q
from django.utils import timezone
from blog.models import (
    User, Manager, TeamLeader, Organization, Organizer,
    OrganizerOrganizationLink, OrganizerInteractionLog, Location, EventCategory, Event,
    EventCategoryMap, EventShift, EventStaff, Application,
    Participation, ChurnHistory, UserComment, EventFeedback, EventRequirement,
    EventRequirementMap, UserRequirementException, Season, ThankYouLetterTemplate, Const,
    DobroRuSharedLink,
)

__all__ = [
    'create_user', 'get_users',
    'get_user_by_telegram_id', 'get_or_create_user_by_telegram_id', 'update_user_fields',
    'create_manager', 'get_managers',
    'create_team_leader', 'get_team_leaders',
    'create_organization', 'get_organizations',
    'create_organizer', 'get_organizers',
    'link_organizer_to_organization', 'get_organizer_links',
    'create_organizer_interaction', 'get_organizer_interactions',
    'create_location', 'get_locations',
    'create_event_category', 'get_event_categories',
    'create_event', 'get_events', 'get_open_events', 'get_active_events',
    'create_dobro_ru_shared_link',
    'add_category_to_event', 'get_event_categories_map',
    'create_event_shift', 'get_event_shifts',
    'assign_staff_to_event', 'get_event_staff',
    'create_application', 'get_applications', 'get_user_active_applications',
    'get_user_application_for_event', 'update_application_status',
    'create_participation', 'get_participations', 'update_participation_status',
    'mark_dobro_ru_logged',
    'get_participations_pending_feedback', 'mark_feedback_requested',
    'create_churn_history', 'get_churn_history',
    'create_user_comment', 'get_user_comments',
    'create_feedback', 'get_feedbacks', 'has_feedback',
    'create_requirement', 'get_requirements',
    'add_requirement_to_event', 'get_event_requirements_map',
    'create_user_exception', 'get_user_exceptions',
    'create_season', 'get_seasons',
    'get_const', 'get_all_consts',
    'get_full_table', 'create_record_generic'
]

# ==============================================================================
# ЧАСТЬ 1: СОЗДАНИЕ ЗАПИСЕЙ (WRITE)
# ==============================================================================

def create_user(
    telegram_id: str,
    telegram_username: Optional[str] = None,
    first_name: Optional[str] = None,
    last_name: Optional[str] = None,
    phone: Optional[str] = None,
    email: Optional[str] = None,
    vk_username: Optional[str] = None,
    birthday: Optional[str] = None,
    about: Optional[str] = None,
    status: str = 'active',
    comment: Optional[str] = None
) -> User:
    """Создает пользователя через кастомный менеджер."""
    return User.objects.create_user(
        telegram_id=telegram_id,
        telegram_username=telegram_username,
        first_name=first_name,
        last_name=last_name,
        phone=phone,
        email=email,
        VK_username=vk_username,
        birthday=birthday,
        about=about,
        status=status,
        comment=comment
    )

def get_user_by_telegram_id(telegram_id: str) -> Optional[User]:
    return User.objects.filter(telegram_id=str(telegram_id)).first()


def get_or_create_user_by_telegram_id(telegram_id: str, telegram_username: Optional[str] = None):
    """Первое касание бота (tg-bot.md §3.1): создаёт пользователя-гостя, если его ещё нет.
    Возвращает (user, created)."""
    user, created = User.objects.get_or_create(
        telegram_id=str(telegram_id),
        defaults={'telegram_username': telegram_username, 'registration_status': 'guest'},
    )
    if not created and telegram_username and user.telegram_username != telegram_username:
        user.telegram_username = telegram_username
        user.save(update_fields=['telegram_username'])
    return user, created


def update_user_fields(user: User, **fields) -> User:
    """Точечное обновление полей пользователя (например, при регистрации в боте)."""
    allowed = set(fields) & {f.name for f in User._meta.get_fields()}
    for name in allowed:
        setattr(user, name, fields[name])
    if allowed:
        user.save(update_fields=list(allowed))
    return user


def create_manager(
    user: User,
    assigned_by: Optional[User] = None,
    total_events: int = 0,
    season_events: int = 0
) -> Manager:
    """Выдает роль менеджера. У менеджера всегда есть и профиль тим-лидера
    (если ещё нет) — чтобы его можно было назначать ТЛ на мероприятия."""
    manager = Manager.objects.create(
        user=user,
        assigned_by=assigned_by,
        total_events=total_events,
        season_events=season_events
    )
    if not TeamLeader.objects.filter(user=user, revoked_at__isnull=True).exists():
        TeamLeader.objects.create(user=user, assigned_by=assigned_by)
    return manager

def create_team_leader(
    user: User,
    assigned_by: Optional[User] = None,
    total_events: int = 0,
    season_events: int = 0
) -> TeamLeader:
    """Выдает роль тим-лидера."""
    return TeamLeader.objects.create(
        user=user,
        assigned_by=assigned_by,
        total_events=total_events,
        season_events=season_events
    )

def create_organization(name: str, description: Optional[str] = None) -> Organization:
    return Organization.objects.create(name=name, description=description)

def create_organizer(
    first_name: str,
    last_name: str,
    user: Optional[User] = None,
    phone: Optional[str] = None,
    email: Optional[str] = None,
    telegram_username: Optional[str] = None,
    vk_username: Optional[str] = None,
    comments: Optional[str] = None
) -> Organizer:
    return Organizer.objects.create(
        user=user,
        first_name=first_name,
        last_name=last_name,
        phone=phone,
        email=email,
        telegram_username=telegram_username,
        VK_username=vk_username,
        comments=comments
    )

def link_organizer_to_organization(organizer: Organizer, organization: Organization) -> OrganizerOrganizationLink:
    return OrganizerOrganizationLink.objects.create(organizer=organizer, organization=organization)

def create_organizer_interaction(
    organizer: Organizer,
    author: Optional[User],
    contact_type: str,
    note: Optional[str] = None
) -> OrganizerInteractionLog:
    return OrganizerInteractionLog.objects.create(
        organizer=organizer, author=author, contact_type=contact_type, note=note
    )

def create_location(name: str) -> Location:
    return Location.objects.create(name=name)

def create_event_category(name: str) -> EventCategory:
    return EventCategory.objects.create(name=name)

def create_event(
    title: str,
    location: Optional[Location] = None,
    organization: Optional[Organization] = None,
    organizer: Optional[Organizer] = None,
    description: Optional[str] = None,
    chat_link: Optional[str] = None,
    post_in_chanel_link: Optional[str] = None,
    event_status: str = 'upcoming',
    people_helped: int = 0,
    dobro_ru_link_kind: str = 'none',
    dobro_ru_link: Optional[str] = None,
    dobro_ru_shared_link: Optional[DobroRuSharedLink] = None,
) -> Event:
    return Event.objects.create(
        title=title,
        location=location,
        organization=organization,
        organizer=organizer,
        description=description,
        chat_link=chat_link,
        post_in_chanel_link=post_in_chanel_link,
        event_status=event_status,
        people_helped=people_helped,
        dobro_ru_link_kind=dobro_ru_link_kind,
        dobro_ru_link=dobro_ru_link,
        dobro_ru_shared_link=dobro_ru_shared_link,
    )


def create_dobro_ru_shared_link(
    title: str,
    url: str,
    link_type: str,
    period_hint: Optional[str] = None,
    is_active: bool = True,
) -> DobroRuSharedLink:
    return DobroRuSharedLink.objects.create(
        title=title, url=url, link_type=link_type, period_hint=period_hint, is_active=is_active,
    )

def add_category_to_event(event: Event, category: EventCategory) -> EventCategoryMap:
    return EventCategoryMap.objects.create(event=event, category=category)

def create_event_shift(
    event: Event,
    day: str,
    start_time: str,
    end_time: str,
    volunteer_needed: int = 0,
    location: Optional[Location] = None
) -> EventShift:
    return EventShift.objects.create(
        event=event,
        day=day,
        start_time=start_time,
        end_time=end_time,
        volunteer_needed=volunteer_needed,
        location=location
    )

def assign_staff_to_event(event: Event, user: User, role: str) -> EventStaff:
    return EventStaff.objects.create(event=event, user=user, staff_role=role)

def create_application(
    event: Event,
    first_name: str,
    last_name: str,
    user: Optional[User] = None,
    telegram_username: Optional[str] = None,
    phone: Optional[str] = None,
    functions: Optional[str] = None,
    shifts: Optional[str] = None,
    role_in_event: Optional[str] = None,
    user_status: Optional[str] = None,
    submitted_at=None,
    status: str = 'new'
) -> Application:
    return Application.objects.create(
        event=event,
        user=user,
        first_name=first_name,
        last_name=last_name,
        telegram_username=telegram_username,
        phone=phone,
        functions=functions,
        shifts=shifts,
        role_in_event=role_in_event,
        user_status=user_status,
        submitted_at=submitted_at or timezone.now(),
        status=status
    )

def create_participation(
    user: User,
    event_day: EventShift,
    status: str = 'attended',
    hours: int = 0,
    full_shift: bool = False,
) -> Participation:
    return Participation.objects.create(
        user=user, event_day=event_day, status=status, hours=hours, full_shift=full_shift,
    )

def create_churn_history(user: User, season: Season, churn_score: float) -> ChurnHistory:
    return ChurnHistory.objects.create(user=user, season=season, churn_score=churn_score)

def create_user_comment(target_user: User, author: Optional[User], text: str, event: Optional[Event] = None) -> UserComment:
    return UserComment.objects.create(target_user=target_user, author=author, event=event, text=text)

def create_feedback(author: User, event: Event, rating: int, comment: Optional[str] = None) -> EventFeedback:
    return EventFeedback.objects.create(author=author, event=event, rating=rating, comment=comment)

def create_requirement(title: str, description: Optional[str] = None) -> EventRequirement:
    return EventRequirement.objects.create(title=title, description=description)

def add_requirement_to_event(event: Event, requirement: EventRequirement) -> EventRequirementMap:
    return EventRequirementMap.objects.create(event=event, requirement=requirement)

def create_user_exception(user: User, requirement: EventRequirement, reason: Optional[str] = None) -> UserRequirementException:
    return UserRequirementException.objects.create(user=user, requirement=requirement, reason=reason)

def create_season(start_date: str, end_date: str) -> Season:
    return Season.objects.create(start_season=start_date, end_season=end_date)


# ==============================================================================
# ЧАСТЬ 2: ЧТЕНИЕ ДАННЫХ (READ / FILTERS)
# ==============================================================================

# --- USERS ---
def get_users(
    name: Optional[str] = None,
    telegram_username: Optional[str] = None,
    telegram_id: Optional[str] = None,
    phone: Optional[str] = None
) -> List[User]:
    filters = Q()
    if name:
        filters &= Q(first_name__icontains=name) | Q(last_name__icontains=name)
    if telegram_username:
        filters &= Q(telegram_username__iexact=telegram_username)
    if telegram_id:
        filters &= Q(telegram_id=telegram_id)
    if phone:
        filters &= Q(phone__icontains=phone)
    return list(User.objects.filter(filters))

# --- MANAGERS ---
def get_managers(user_id: Optional[int] = None, is_active_only: bool = True) -> List[Manager]:
    qs = Manager.objects.select_related('user')
    if user_id:
        qs = qs.filter(user_id=user_id)
    if is_active_only:
        qs = qs.filter(revoked_at__isnull=True)
    return list(qs)

# --- TEAM LEADERS ---
def get_team_leaders(user_id: Optional[int] = None, is_active_only: bool = True) -> List[TeamLeader]:
    qs = TeamLeader.objects.select_related('user')
    if user_id:
        qs = qs.filter(user_id=user_id)
    if is_active_only:
        qs = qs.filter(revoked_at__isnull=True)
    return list(qs)

# --- ORGANIZATIONS ---
def get_organizations(name_filter: Optional[str] = None) -> List[Organization]:
    qs = Organization.objects.all()
    if name_filter:
        qs = qs.filter(name__icontains=name_filter)
    return list(qs)

# --- ORGANIZERS ---
def get_organizers(name_filter: Optional[str] = None) -> List[Organizer]:
    qs = Organizer.objects.all()
    if name_filter:
        qs = qs.filter(first_name__icontains=name_filter) | qs.filter(last_name__icontains=name_filter)
    return list(qs)

# --- LINKS ---
def get_organizer_links(organizer_id: Optional[int] = None, organization_id: Optional[int] = None) -> List[OrganizerOrganizationLink]:
    qs = OrganizerOrganizationLink.objects.all()
    if organizer_id:
        qs = qs.filter(organizer_id=organizer_id)
    if organization_id:
        qs = qs.filter(organization_id=organization_id)
    return list(qs)

def get_organizer_interactions(organizer_id: Optional[int] = None) -> List[OrganizerInteractionLog]:
    qs = OrganizerInteractionLog.objects.select_related('author').order_by('-created_at')
    if organizer_id:
        qs = qs.filter(organizer_id=organizer_id)
    return list(qs)

# --- LOCATIONS ---
def get_locations(name_filter: Optional[str] = None) -> List[Location]:
    qs = Location.objects.all()
    if name_filter:
        qs = qs.filter(name__icontains=name_filter)
    return list(qs)

# --- CATEGORIES ---
def get_event_categories(name_filter: Optional[str] = None) -> List[EventCategory]:
    qs = EventCategory.objects.all()
    if name_filter:
        qs = qs.filter(name__icontains=name_filter)
    return list(qs)

# --- EVENTS ---
def get_events(title_filter: Optional[str] = None) -> List[Event]:
    qs = Event.objects.all()
    if title_filter:
        qs = qs.filter(title__icontains=title_filter)
    return list(qs)

# --- EVENTS (доп. выборки для бота) ---
def get_open_events(limit: Optional[int] = None) -> List[Event]:
    """Опубликованные меро хотя бы с одной сменой с открытым набором (tg-bot.md §4.1)."""
    qs = (
        Event.objects.filter(
            shifts__recruit_status='open',
            event_status__in=['upcoming', 'ongoing'],
            published_at__isnull=False,
        )
        .select_related('location')
        .prefetch_related('shifts')
        .distinct()
        .order_by('created_at')
    )
    return list(qs[:limit]) if limit else list(qs)


def get_active_events() -> List[Event]:
    """Не завершённые меро (для карточек с контактами организатора у менеджера)."""
    return list(
        Event.objects.filter(event_status__in=['upcoming', 'ongoing'])
        .select_related('location', 'organization', 'organizer')
    )


# --- CATEGORY MAP ---
def get_event_categories_map(event_id: Optional[int] = None) -> List[EventCategoryMap]:
    qs = EventCategoryMap.objects.all()
    if event_id:
        qs = qs.filter(event_id=event_id)
    return list(qs)

# --- EVENT SHIFTS ---
def get_event_shifts(event_id: Optional[int] = None) -> List[EventShift]:
    qs = EventShift.objects.all()
    if event_id:
        qs = qs.filter(event_id=event_id)
    return list(qs)

# --- STAFF ---
def get_event_staff(event_id: Optional[int] = None, user_id: Optional[int] = None) -> List[EventStaff]:
    qs = EventStaff.objects.all()
    if event_id:
        qs = qs.filter(event_id=event_id)
    if user_id:
        qs = qs.filter(user_id=user_id)
    return list(qs)

# --- APPLICATIONS ---
def get_applications(
    status_filter: Optional[str] = None,
    applicant_name: Optional[str] = None,
    event_id: Optional[int] = None,
) -> List[Application]:
    qs = Application.objects.select_related('event', 'user').order_by('-created_at')
    if status_filter:
        qs = qs.filter(status=status_filter)
    if applicant_name:
        qs = qs.filter(first_name__icontains=applicant_name) | qs.filter(last_name__icontains=applicant_name)
    if event_id:
        qs = qs.filter(event_id=event_id)
    return list(qs)

def get_user_active_applications(user_id: int) -> List[Application]:
    """Активные заявки волонтёра — только new/approved (tg-bot.md §4.4), и только на
    мероприятия, которые ещё идут или предстоят (`compute_status` in upcoming/ongoing).
    Заявки на уже прошедшие/завершённые меро не показываются — волонтёру нечего с ними
    делать, они провисели бы в списке бесконечно."""
    apps = (
        Application.objects.filter(user_id=user_id, status__in=['new', 'approved'])
        .select_related('event')
        .prefetch_related('event__shifts')
        .order_by('-created_at')
    )
    return [a for a in apps if a.event.compute_status() in ('upcoming', 'ongoing')]


def get_user_application_for_event(user_id: int, event_id: int) -> Optional[Application]:
    return Application.objects.filter(
        user_id=user_id, event_id=event_id, status__in=['new', 'approved']
    ).first()


def update_application_status(application: Application, status: str, assigned_by: Optional[User] = None) -> Application:
    application.status = status
    application.assigned_at = timezone.now()
    if assigned_by:
        application.assigned_by = assigned_by
    application.save(update_fields=['status', 'assigned_at', 'assigned_by'])
    return application


# --- PARTICIPATIONS ---
def get_participations(
    status_filter: Optional[str] = None,
    volunteer_name: Optional[str] = None,
    event_id: Optional[int] = None,
) -> List[Participation]:
    qs = Participation.objects.select_related('user', 'event_day')
    if status_filter:
        qs = qs.filter(status=status_filter)
    if volunteer_name:
        qs = qs.filter(Q(user__first_name__icontains=volunteer_name) | Q(user__last_name__icontains=volunteer_name))
    if event_id:
        qs = qs.filter(event_day__event_id=event_id)
    return list(qs)


def get_participations_pending_feedback(before) -> List[Participation]:
    """Присутствовавшие участники завершённых дней меро старше `before`, кому ещё
    не отправляли запрос "оцени мероприятие" (tg-bot.md §4.6)."""
    return list(
        Participation.objects.filter(
            status='attended',
            feedback_requested_at__isnull=True,
            event_day__end_time__lte=before,
        ).select_related('user', 'event_day__event')
    )


def mark_feedback_requested(participation: Participation) -> None:
    participation.feedback_requested_at = timezone.now()
    participation.save(update_fields=['feedback_requested_at'])


def update_participation_status(
    participation: Participation,
    status: str,
    hours: Optional[int] = None,
    full_shift: Optional[bool] = None,
) -> Participation:
    update_fields = ['status']
    participation.status = status
    if hours is not None:
        participation.hours = hours
        update_fields.append('hours')
    if full_shift is not None:
        participation.full_shift = full_shift
        update_fields.append('full_shift')
    participation.save(update_fields=update_fields)
    return participation


def mark_dobro_ru_logged(participation: Participation, logged_by: Optional[User] = None) -> Participation:
    """Ручная простановка часов на Добро.ру по участию волонтёра (db.md, правило 7e)."""
    participation.dobro_ru_logged = True
    participation.dobro_ru_logged_at = timezone.now()
    participation.dobro_ru_logged_by = logged_by
    participation.save(update_fields=['dobro_ru_logged', 'dobro_ru_logged_at', 'dobro_ru_logged_by'])
    return participation

# --- FEEDBACKS ---
def get_feedbacks(event_id: Optional[int] = None, author_id: Optional[int] = None) -> List[EventFeedback]:
    qs = EventFeedback.objects.all()
    if event_id:
        qs = qs.filter(event_id=event_id)
    if author_id:
        qs = qs.filter(author_id=author_id)
    return list(qs)

def has_feedback(author_id: int, event_id: int) -> bool:
    return EventFeedback.objects.filter(author_id=author_id, event_id=event_id).exists()


# --- REQUIREMENTS ---
def get_requirements(title_filter: Optional[str] = None) -> List[EventRequirement]:
    qs = EventRequirement.objects.all()
    if title_filter:
        qs = qs.filter(title__icontains=title_filter)
    return list(qs)

def get_event_requirements_map(event_id: Optional[int] = None) -> List[EventRequirementMap]:
    qs = EventRequirementMap.objects.all()
    if event_id:
        qs = qs.filter(event_id=event_id)
    return list(qs)

# --- EXCEPTIONS ---
def get_user_exceptions(user_id: Optional[int] = None) -> List[UserRequirementException]:
    qs = UserRequirementException.objects.all()
    if user_id:
        qs = qs.filter(user_id=user_id)
    return list(qs)

# --- SEASONS ---
def get_seasons() -> List[Season]:
    return list(Season.objects.all().order_by('-start_season'))

# --- CHURN HISTORY ---
def get_churn_history(user_id: Optional[int] = None, season_id: Optional[int] = None) -> List[ChurnHistory]:
    qs = ChurnHistory.objects.select_related('season')
    if user_id:
        qs = qs.filter(user_id=user_id)
    if season_id:
        qs = qs.filter(season_id=season_id)
    return list(qs.order_by('-created_at'))

# --- USER COMMENTS ---
def get_user_comments(target_user_id: Optional[int] = None) -> List[UserComment]:
    qs = UserComment.objects.select_related('author', 'event')
    if target_user_id:
        qs = qs.filter(target_user_id=target_user_id)
    return list(qs.order_by('-created_at'))

# --- CONST ---
def get_const(name: str, default: Optional[float] = None) -> float:
    try:
        return Const.objects.get(name=name).value
    except Const.DoesNotExist:
        if default is not None:
            return default
        raise

def get_all_consts() -> List[Const]:
    return list(Const.objects.all().order_by('name'))


# ==============================================================================
# ЧАСТЬ 3: ОБЩИЕ ФУНКЦИИ (GENERIC)
# ==============================================================================

TABLE_MODEL_MAP: Dict[str, Any] = {
    'users': User,
    'managers': Manager,
    'team_leaders': TeamLeader,
    'organizations': Organization,
    'organizers': Organizer,
    'organizer_organization_link': OrganizerOrganizationLink,
    'organizer_interaction_log': OrganizerInteractionLog,
    'locations': Location,
    'event_categories': EventCategory,
    'events': Event,
    'event_category_map': EventCategoryMap,
    'event_shifts': EventShift,
    'event_staff': EventStaff,
    'applications': Application,
    'participations': Participation,
    'churn_history': ChurnHistory,
    'user_comments': UserComment,
    'event_feedbacks': EventFeedback,
    'event_requirements': EventRequirement,
    'event_requirement_map': EventRequirementMap,
    'user_requirement_exceptions': UserRequirementException,
    'seasons': Season,
    'thank_you_letter_templates': ThankYouLetterTemplate,
    'dobro_ru_shared_links': DobroRuSharedLink,
    'const': Const,
}

def get_full_table(table_name: str) -> List[Dict[str, Any]]:
    """Возвращает все записи таблицы в виде списка словарей."""
    model = TABLE_MODEL_MAP.get(table_name.lower())
    if not model:
        raise ValueError(f"Неизвестная таблица: '{table_name}'. Доступные: {list(TABLE_MODEL_MAP.keys())}")
    return list(model.objects.all().values())

def create_record_generic(table_name: str, **data) -> Any:
    """Универсальное создание записи (использовать с осторожностью)."""
    model = TABLE_MODEL_MAP.get(table_name.lower())
    if not model:
        raise ValueError(f"Неизвестная таблица: '{table_name}'")
    
    if model == User:
        if 'telegram_id' not in data:
            raise ValueError("Для создания пользователя обязательно поле telegram_id")
        return User.objects.create_user(**data)
        
    return model.objects.create(**data)


