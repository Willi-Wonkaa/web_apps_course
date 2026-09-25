"""Отчёт за период и вспомогательная аналитика (reports.md раздел 7).

Все функции принимают либо диапазон дат (date_from/date_to), либо явный список
мероприятий (event_ids) — режимы «по периоду» и «по мероприятиям» взаимоисключающие
(reports_template.md, «Выбор охвата»). Диапазон и список мероприятий приводятся к
единому Q-фильтру через `_scope_filter`, чтобы не дублировать ветвление в каждой функции.
"""
import math
from datetime import timedelta

from django.db.models import Avg, Count, Q, Sum

from blog.models import Application, Event, EventShift, Participation, User


def _events_in_scope(date_from=None, date_to=None, event_ids=None):
    """Завершённые мероприятия, попадающие в охват (по периоду или явному списку)."""
    qs = Event.objects.filter(event_status='finished')
    if event_ids:
        return qs.filter(id__in=event_ids).distinct()
    return qs.filter(shifts__day__gte=date_from, shifts__day__lte=date_to).distinct()


def _shifts_in_scope(date_from=None, date_to=None, event_ids=None):
    qs = EventShift.objects.filter(event__event_status='finished')
    if event_ids:
        return qs.filter(event_id__in=event_ids)
    return qs.filter(day__gte=date_from, day__lte=date_to)


def period_report(date_from=None, date_to=None, prev_from=None, prev_to=None, event_ids=None):
    events = _events_in_scope(date_from, date_to, event_ids)
    shifts = _shifts_in_scope(date_from, date_to, event_ids)

    participations = Participation.objects.filter(event_day__in=shifts)
    attended = participations.filter(status='attended')

    total_hours = attended.aggregate(s=Sum('hours'))['s'] or 0
    total_events = events.count()
    unique_volunteers = attended.values('user').distinct().count()
    closed_shifts = attended.count()
    new_volunteers = _new_volunteers_count(date_from, date_to, event_ids)
    people_helped = events.aggregate(s=Sum('people_helped'))['s'] or 0

    top_volunteers = list(
        attended.values('user__id', 'user__telegram_username', 'user__first_name', 'user__last_name')
        .annotate(hours=Sum('hours'))
        .order_by('-hours')[:10]
    )
    # Готовое имя без "None" (у части профилей всё ФИО в first_name, а last_name пуст).
    for v in top_volunteers:
        full = f"{v['user__first_name'] or ''} {v['user__last_name'] or ''}".strip()
        v['name'] = full or v['user__telegram_username'] or '—'

    category_breakdown = list(
        attended.values(
            name=models_f('event_day__event__category_map__category__name'),
        ).annotate(
            volunteers=Count('user', distinct=True),
            hours=Sum('hours'),
            events=Count('event_day__event', distinct=True),
        ).order_by('-volunteers')
    )
    top_categories = [{'event_day__event__category_map__category__name': c['name'], 'count': c['volunteers']} for c in category_breakdown[:10]]

    # Воронка набора
    apps_scope = Application.objects.filter(event__in=events) if event_ids else Application.objects.filter(
        created_at__date__gte=date_from, created_at__date__lte=date_to,
    )
    submitted = apps_scope.count()
    approved = apps_scope.filter(status='approved').count()
    rejected = apps_scope.filter(status='rejected').count()
    attended_count = attended.count()
    unexcused = participations.filter(status='absent_unexcused').count()
    conversion = (attended_count / submitted * 100) if submitted else 0

    # Retention / churn
    from blog.models import Const
    threshold = Const.objects.filter(name='churn_blacklist_threshold').first()
    threshold_value = threshold.value if threshold else 5
    active_total = User.objects.filter(status='active').count() or 1
    churned = User.objects.filter(churn_score__gte=threshold_value).count()
    churn_rate = churned / active_total * 100
    if event_ids:
        new_blacklisted = 0  # без временного охвата «новых в blacklist» не определить
    else:
        new_blacklisted = User.objects.filter(
            status='blacklisted', created_at__date__gte=date_from, created_at__date__lte=date_to,
        ).count()

    churn_histogram = _churn_histogram(threshold_value)

    # Проблемные зоны
    low_rated_events = list(
        events.annotate(avg_rating=Avg('feedbacks__rating'), fb_count=Count('feedbacks'))
        .filter(avg_rating__lt=6, fb_count__gt=0)
        .values('id', 'title', 'avg_rating', 'fb_count')
    )

    at_risk_users = list(
        User.objects.filter(churn_score__gte=threshold_value * 0.8)
        .values('id', 'telegram_username', 'first_name', 'last_name', 'churn_score')
        .order_by('-churn_score')[:20]
    )

    comparison = None
    if prev_from and prev_to and not event_ids:
        prev = period_report_basic(prev_from, prev_to)
        comparison = {
            'date_from': prev_from,
            'date_to': prev_to,
            'hours_delta': total_hours - prev['total_hours'],
            'events_delta': total_events - prev['total_events'],
            'volunteers_delta': unique_volunteers - prev['unique_volunteers'],
            'new_volunteers_delta': new_volunteers - prev['new_volunteers'],
            'hours_delta_pct': _pct_delta(total_hours, prev['total_hours']),
            'events_delta_pct': _pct_delta(total_events, prev['total_events']),
            'volunteers_delta_pct': _pct_delta(unique_volunteers, prev['unique_volunteers']),
            'new_volunteers_delta_pct': _pct_delta(new_volunteers, prev['new_volunteers']),
        }

    return {
        'total_hours': total_hours,
        'total_events': total_events,
        'unique_volunteers': unique_volunteers,
        'closed_shifts': closed_shifts,
        'new_volunteers': new_volunteers,
        'people_helped': people_helped,
        'top_volunteers': top_volunteers,
        'top_categories': top_categories,
        'category_breakdown': category_breakdown,
        'funnel': {
            'submitted': submitted,
            'approved': approved,
            'rejected': rejected,
            'attended': attended_count,
            'unexcused': unexcused,
            'conversion_pct': round(conversion, 1),
        },
        'retention': {
            'churn_rate_pct': round(churn_rate, 1),
            'new_blacklisted': new_blacklisted,
        },
        'churn_histogram': churn_histogram,
        'churn_threshold': threshold_value,
        'problem_zones': {
            'low_rated_events': low_rated_events,
            'at_risk_users': at_risk_users,
        },
        'comparison': comparison,
    }


def models_f(name):
    """Мелкий хелпер, чтобы values(name=...) читался как алиас, а не голая строка."""
    from django.db.models import F
    return F(name)


def _pct_delta(current, previous):
    if not previous:
        return None
    return round((current - previous) / previous * 100, 1)


def _new_volunteers_count(date_from, date_to, event_ids):
    if event_ids:
        return 0  # «новых волонтёров» без временных границ не определить
    return User.objects.filter(created_at__date__gte=date_from, created_at__date__lte=date_to).count()


def _churn_histogram(threshold_value, bucket_size=1.0, max_buckets=8):
    """Гистограмма churn_score волонтёров по бакетам фиксированного шага —
    для графика «Распределение churn» (reports_template.md §1B, блок 5)."""
    buckets = []
    for i in range(max_buckets):
        low = i * bucket_size
        high = low + bucket_size
        if i == max_buckets - 1:
            count = User.objects.filter(churn_score__gte=low).count()
            label = f'{low:g}+'
        else:
            count = User.objects.filter(churn_score__gte=low, churn_score__lt=high).count()
            label = f'{low:g}–{high:g}'
        buckets.append({'label': label, 'low': low, 'high': high, 'count': count})
    return buckets


def period_report_basic(date_from, date_to):
    """Облегчённая версия для сравнения с предыдущим периодом (без рекурсии)."""
    attended = Participation.objects.filter(
        event_day__day__gte=date_from, event_day__day__lte=date_to, status='attended',
        event_day__event__event_status='finished',
    )
    return {
        'total_hours': attended.aggregate(s=Sum('hours'))['s'] or 0,
        'total_events': Event.objects.filter(
            shifts__day__gte=date_from, shifts__day__lte=date_to, event_status='finished',
        ).distinct().count(),
        'unique_volunteers': attended.values('user').distinct().count(),
        'new_volunteers': User.objects.filter(
            created_at__date__gte=date_from, created_at__date__lte=date_to
        ).count(),
    }


def previous_period_range(date_from, date_to, mode='adjacent'):
    """Диапазон «прошлого периода» для блока сравнения — два режима переключателя
    (reports_template.md): смежный интервал той же длины, либо тот же интервал год назад."""
    if mode == 'year_ago':
        try:
            prev_from = date_from.replace(year=date_from.year - 1)
            prev_to = date_to.replace(year=date_to.year - 1)
        except ValueError:
            # 29 февраля и т.п. — сдвигаем на ближайший валидный день.
            prev_from = date_from - timedelta(days=365)
            prev_to = date_to - timedelta(days=365)
        return prev_from, prev_to

    span = (date_to - date_from).days
    prev_to = date_from - timedelta(days=1)
    prev_from = prev_to - timedelta(days=span)
    return prev_from, prev_to


def weekly_dynamics(date_from=None, date_to=None, event_ids=None):
    """Динамика по неделям: часы и закрытые смены на каждую неделю периода.
    В режиме «по мероприятиям» не строится — нет временной оси периода."""
    if event_ids:
        return []

    weeks = []
    max_hours = 0
    cursor = date_from
    while cursor <= date_to:
        week_end = min(cursor + timedelta(days=6), date_to)
        attended = Participation.objects.filter(
            event_day__day__gte=cursor, event_day__day__lte=week_end, status='attended',
            event_day__event__event_status='finished',
        )
        hours = attended.aggregate(s=Sum('hours'))['s'] or 0
        shifts = attended.count()
        max_hours = max(max_hours, hours)
        weeks.append({
            'from': cursor,
            'to': week_end,
            'hours': hours,
            'shifts': shifts,
        })
        cursor = week_end + timedelta(days=1)

    for week in weeks:
        week['pct'] = round(week['hours'] / max_hours * 100) if max_hours else 0
    return weeks


def recruitment_efficiency(date_from=None, date_to=None, event_ids=None):
    """% закрытия слотов по каждой СМЕНЕ (не только по меро в целом) — reports_template.md
    §1 блок 3. Считается только по завершённым мероприятиям."""
    shifts = _shifts_in_scope(date_from, date_to, event_ids).select_related('event').order_by('event__title', 'day')
    result = []
    avg_recruit_days = []
    for shift in shifts:
        needed = shift.volunteer_needed or 0
        attended = Participation.objects.filter(event_day=shift, status='attended').count()
        pct = (attended / needed * 100) if needed else 0
        result.append({
            'event_id': shift.event_id,
            'shift_id': shift.id,
            'title': shift.event.title,
            'day': shift.day,
            'needed': needed,
            'attended': attended,
            'pct_closed': round(pct, 1),
            'understaffed': needed > 0 and (attended / needed) < 0.8,
        })

        last_approved = Application.objects.filter(
            event=shift.event, status='approved', assigned_at__isnull=False,
        ).order_by('-assigned_at').first()
        if last_approved and last_approved.assigned_at:
            days = (shift.day - last_approved.assigned_at.date()).days
            if days >= 0:
                avg_recruit_days.append(days)

    avg_recruit_time = round(sum(avg_recruit_days) / len(avg_recruit_days), 1) if avg_recruit_days else None
    return {'shifts': result, 'avg_recruit_days': avg_recruit_time}


def staff_load(date_from=None, date_to=None, event_ids=None):
    """Нагрузка персонала: меро/заявки на менеджера и тим-лидера за охват —
    обработка заявок считается и для менеджеров, и для тим-лидеров (reports.md)."""
    from blog.models import EventStaff

    events = _events_in_scope(date_from, date_to, event_ids)
    apps_scope = Application.objects.filter(event__in=events) if event_ids else Application.objects.filter(
        created_at__date__gte=date_from, created_at__date__lte=date_to,
    )

    processed_apps = list(
        apps_scope.filter(assigned_by__isnull=False, assigned_at__isnull=False)
        .values('assigned_by__id', 'created_at', 'assigned_at')
    )
    processing_by_user = {}
    for app in processed_apps:
        uid = app['assigned_by__id']
        seconds = (app['assigned_at'] - app['created_at']).total_seconds()
        processing_by_user.setdefault(uid, []).append(seconds)

    counts_by_user = {}
    for app in apps_scope.filter(assigned_by__isnull=False).values('assigned_by__id'):
        counts_by_user[app['assigned_by__id']] = counts_by_user.get(app['assigned_by__id'], 0) + 1

    def _staff_with_apps(role):
        staff = list(
            EventStaff.objects.filter(staff_role=role, event__in=events)
            .values('user__id', 'user__first_name', 'user__last_name')
            .annotate(event_count=Count('event', distinct=True))
        )
        for row in staff:
            uid = row['user__id']
            row['applications_processed'] = counts_by_user.get(uid, 0)
            durations = processing_by_user.get(uid)
            row['avg_processing_hours'] = round(sum(durations) / len(durations) / 3600, 1) if durations else None
            row['name'] = f"{row['user__first_name'] or ''} {row['user__last_name'] or ''}".strip() or '—'
        return staff

    managers = _staff_with_apps('manager')
    team_leads = _staff_with_apps('team_lead')

    apps_by_manager = list(
        apps_scope.filter(assigned_by__isnull=False)
        .values('assigned_by__id', 'assigned_by__first_name', 'assigned_by__last_name')
        .annotate(processed=Count('id'))
    )
    return {'managers': managers, 'team_leads': team_leads, 'applications_processed': apps_by_manager}


def application_timing(date_from=None, date_to=None, event_ids=None, bucket_hours=(1, 6, 24, 72)):
    """Тайминг заявок относительно публикации меро — reports_template.md §1B блок 8.

    Для каждого мероприятия «ноль» — самое раннее из post_published_at / published_at.
    Возвращает кумулятивную кривую заявок по времени от нуля и гистограмму плотности
    по бакетам фиксированного шага."""
    events = _events_in_scope(date_from, date_to, event_ids).filter(
        Q(published_at__isnull=False) | Q(post_published_at__isnull=False)
    )

    points = []  # (event_id, hours_since_zero)
    per_event_zero = {}
    for event in events:
        candidates = [t for t in (event.published_at, event.post_published_at) if t]
        if not candidates:
            continue
        zero = min(candidates)
        per_event_zero[event.id] = {
            'zero': zero,
            'post_offset_h': (event.post_published_at - zero).total_seconds() / 3600 if event.post_published_at else None,
            'announce_offset_h': (event.published_at - zero).total_seconds() / 3600 if event.published_at else None,
            'zero_is': 'post' if event.post_published_at == zero else 'announce',
        }

    apps = Application.objects.filter(event_id__in=per_event_zero.keys(), submitted_at__isnull=False)
    for app in apps:
        zero = per_event_zero[app.event_id]['zero']
        delta_h = (app.submitted_at - zero).total_seconds() / 3600
        if delta_h >= 0:
            points.append(delta_h)

    points.sort()
    cumulative = []
    for i, h in enumerate(points, start=1):
        cumulative.append({'hours': round(h, 1), 'count': i})

    bucket_edges = [0, *bucket_hours, math.inf]
    density = []
    for i in range(len(bucket_edges) - 1):
        low, high = bucket_edges[i], bucket_edges[i + 1]
        count = sum(1 for h in points if low <= h < high)
        label = f'{low:g}–{high:g}ч' if high != math.inf else f'>{low:g}ч'
        density.append({'label': label, 'count': count})

    return {
        'events': per_event_zero,
        'cumulative': cumulative,
        'density': density,
        'total_applications': len(points),
    }


def post_publication_timing(date_from=None, date_to=None, event_ids=None):
    """Время публикации постов по часам суток / дням недели (reports.md, график)."""
    events = _events_in_scope(date_from, date_to, event_ids).filter(post_published_at__isnull=False)
    by_hour = [0] * 24
    by_weekday = [0] * 7  # 0 = понедельник
    for event in events.values_list('post_published_at', flat=True):
        by_hour[event.hour] += 1
        by_weekday[event.weekday()] += 1
    return {'by_hour': by_hour, 'by_weekday': by_weekday}
