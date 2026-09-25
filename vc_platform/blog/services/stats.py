"""Пересчёт кэш-агрегатов пользователя и churn (db.md, бизнес-правила 1, 9)."""
import math
from datetime import timedelta

from django.db.models import Sum, Count
from django.utils import timezone

from blog.models import Participation, Const, Season, ChurnHistory


def _const(name: str, default: float) -> float:
    try:
        return Const.objects.get(name=name).value
    except Const.DoesNotExist:
        return default


def current_season():
    from django.db.models import Q
    today = timezone.now().date()
    return (
        Season.objects.filter(start_season__lte=today)
        .filter(Q(end_season__isnull=True) | Q(end_season__gte=today))
        .order_by('-start_season')
        .first()
    )


def recalc_user_aggregates(user):
    """Пересчитывает total_hours/season_hours/*_event_count по participations.status='attended'."""
    attended = Participation.objects.filter(user=user, status='attended')

    totals = attended.aggregate(hours=Sum('hours'), count=Count('id'))
    user.total_hours = totals['hours'] or 0
    user.total_event_count = totals['count'] or 0

    season = current_season()
    if season:
        season_qs = attended.filter(
            event_day__day__gte=season.start_season,
        )
        if season.end_season:
            season_qs = season_qs.filter(event_day__day__lte=season.end_season)
        season_totals = season_qs.aggregate(hours=Sum('hours'), count=Count('id'))
        user.season_hours = season_totals['hours'] or 0
        user.season_event_count = season_totals['count'] or 0
    else:
        user.season_hours = 0
        user.season_event_count = 0

    last = attended.order_by('-event_day__day').first()
    user.last_event_at = last.created_at if last else user.last_event_at

    user.save(update_fields=[
        'total_hours', 'total_event_count', 'season_hours', 'season_event_count', 'last_event_at'
    ])


def apply_churn_no_show(user):
    """+churn_no_show_penalty за неявку без предупреждения (правило 9)."""
    penalty = _const('churn_no_show_penalty', 1)
    user.churn_score = (user.churn_score or 0) + penalty
    _apply_blacklist_if_needed(user)
    user.save(update_fields=['churn_score', 'status'])


def apply_churn_activity_decay(user):
    """Списание за активность: -churn_activity_base / e^(N меро за последний месяц + 1)."""
    base = _const('churn_activity_base', 3)
    month_ago = timezone.now() - timedelta(days=30)
    n = Participation.objects.filter(
        user=user, status='attended', created_at__gte=month_ago
    ).count()
    decay = base / math.exp(n + 1)
    user.churn_score = max(0.0, (user.churn_score or 0) - decay)
    user.save(update_fields=['churn_score'])


def _apply_blacklist_if_needed(user):
    threshold = _const('churn_blacklist_threshold', 5)
    if (user.churn_score or 0) >= threshold:
        user.status = 'blacklisted'


def is_pgas_star(user) -> bool:
    threshold = _const('pgas_hours_threshold', 100)
    return (user.season_hours or 0) >= threshold


def recruit_status_for_shift(shift):
    """closed, когда approved >= recruit_close_ratio * volunteer_needed смены (правило 10).

    Заявка сейчас подаётся на мероприятие целиком (выбор конкретных смен ещё не
    реализован — см. ApplicationForm), поэтому одобренные заявки считаются общими
    по всему мероприятию и сравниваются с потребностью именно этой смены."""
    from blog.models import Application
    ratio = _const('recruit_close_ratio', 1.2)
    needed = shift.volunteer_needed or 0
    approved = Application.objects.filter(event=shift.event, status='approved').count()
    if needed and approved >= ratio * needed:
        return 'closed'
    return 'open'


def close_season(season):
    """Закрытие сезона (правила 9/11): снимок ненулевого churn в churn_history,
    carryover, обнуление season_*; авто-создание бессрочного сезона, если
    следующий не задан. Возвращает созданный сезон или None."""
    from blog.models import User
    min_seasons = _const('churn_carryover_min_seasons', 2)
    for user in User.objects.filter(churn_score__gt=0):
        ChurnHistory.objects.create(user=user, season=season, churn_score=user.churn_score)
        past_seasons = ChurnHistory.objects.filter(user=user).count()
        if past_seasons >= min_seasons:
            user.churn_score = user.churn_score / 2
        else:
            user.churn_score = 0
        user.save(update_fields=['churn_score'])

    User.objects.update(season_hours=0, season_event_count=0)

    today = timezone.now().date()
    if not season.end_season or season.end_season > today:
        season.end_season = today
        season.save(update_fields=['end_season'])

    next_start = season.end_season + timedelta(days=1)
    has_next = Season.objects.filter(start_season__gte=next_start).exists()
    if not has_next:
        # Бессрочный сезон: end_season=NULL — плашка «нужно задать дату конца».
        return Season.objects.create(start_season=next_start, end_season=None)
    return None
