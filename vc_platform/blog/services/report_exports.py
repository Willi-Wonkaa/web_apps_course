"""Сырые построчные выгрузки и данные для отчётов-документов
(reports_template.md §2.1, §2.2, §4, §5).

Отделено от `reports.py` (там — агрегатная аналитика за период). Здесь — построчные
данные: заявки, мероприятия, отчёт по одному меро, простановка часов на Добро.ру,
а также сборка Excel-книг (используется и вьюхой экспорта, и комплексным ZIP-отчётом).
Охват задаётся так же — либо период (date_from/date_to), либо список event_ids.
"""
from django.db.models import Avg, Count, Sum

from blog.models import (
    Application, Event, EventShift, EventStaff, Participation,
)
from blog.services import reports as reports_service


def _fmt_dt(value):
    """openpyxl не умеет tz-aware datetime — отдаём строкой."""
    if value is None:
        return '—'
    if hasattr(value, 'strftime'):
        return value.strftime('%d.%m.%Y %H:%M') if hasattr(value, 'hour') else value.strftime('%d.%m.%Y')
    return str(value)


def _staff_names(event, role):
    names = []
    for st in event.staff.filter(staff_role=role).select_related('user'):
        names.append(st.user.full_name)
    return ', '.join(names) or '—'


def applications_rows(date_from=None, date_to=None, event_ids=None):
    """Сырая построчная выгрузка заявок (reports_template.md §2.1).

    Одна approved-заявка разворачивается в несколько строк — по строке на смену участия
    (participations). new/rejected/cancelled — одна строка без участия."""
    events = reports_service._events_in_scope(date_from, date_to, event_ids)
    if event_ids:
        apps = Application.objects.filter(event__in=events)
    else:
        apps = Application.objects.filter(
            created_at__date__gte=date_from, created_at__date__lte=date_to,
        )
    apps = apps.select_related('event', 'user', 'assigned_by').prefetch_related('selected_shifts')

    rows = []
    for app in apps:
        base = {
            'id': app.id,
            'name': f"{app.first_name or ''} {app.last_name or ''}".strip(),
            'telegram': app.telegram_username or '',
            'phone': app.phone or '',
            'user_id': app.user_id or '—',
            'event': app.event.title,
            'functions': app.functions or '',
            'role': app.role_in_event or '',
            'submitted_at': app.submitted_at,
            'created_at': app.created_at,
            'status': app.get_status_display(),
            'assigned_by': app.assigned_by.full_name if app.assigned_by else '—',
            'assigned_at': app.assigned_at,
        }

        # Участия этого волонтёра по сменам данного меро (если approved и есть профиль).
        parts = []
        if app.user_id:
            parts = list(
                Participation.objects.filter(user_id=app.user_id, event_day__event=app.event)
                .select_related('event_day')
            )
        if parts:
            for p in parts:
                row = dict(base)
                row.update({
                    'shift_day': p.event_day.day,
                    'attended': p.get_status_display(),
                    'full_shift': 'да' if p.full_shift else 'нет',
                    'hours': p.hours,
                })
                rows.append(row)
        else:
            row = dict(base)
            row.update({'shift_day': None, 'attended': '—', 'full_shift': '—', 'hours': '—'})
            rows.append(row)
    return rows


def events_rows(date_from=None, date_to=None, event_ids=None):
    """Сырая построчная выгрузка мероприятий (reports_template.md §2.2)."""
    events = reports_service._events_in_scope(date_from, date_to, event_ids).select_related(
        'organization', 'organizer'
    ).prefetch_related('shifts', 'category_map__category', 'staff__user', 'feedbacks')

    rows = []
    for event in events:
        shifts = list(event.shifts.all())
        days = [s.day for s in shifts]
        needed = sum(s.volunteer_needed or 0 for s in shifts)
        attended = Participation.objects.filter(event_day__event=event, status='attended')
        attended_count = attended.count()
        total_hours = attended.aggregate(s=Sum('hours'))['s'] or 0
        apps = Application.objects.filter(event=event)
        rating = event.feedbacks.aggregate(a=Avg('rating'), c=Count('id'))
        categories = ', '.join(m.category.name for m in event.category_map.all()) or '—'
        locations = ', '.join(sorted({s.location.name for s in shifts if s.location_id})) or '—'
        rows.append({
            'id': event.id,
            'title': event.title,
            'organization': event.organization.name if event.organization else '—',
            'organizer': f"{event.organizer.first_name} {event.organizer.last_name}" if event.organizer else '—',
            'categories': categories,
            'date_from': min(days) if days else None,
            'date_to': max(days) if days else None,
            'shift_count': len(shifts),
            'locations': locations,
            'event_status': event.get_event_status_display(),
            'needed': needed,
            'submitted': apps.count(),
            'approved': apps.filter(status='approved').count(),
            'attended': attended_count,
            'pct_closed': round(attended_count / needed * 100, 1) if needed else 0,
            'total_hours': total_hours,
            'people_helped': event.people_helped if event.people_helped is not None else '—',
            'rating': round(rating['a'], 1) if rating['a'] is not None else '—',
            'rating_count': rating['c'],
            'managers': _staff_names(event, 'manager'),
            'team_leads': _staff_names(event, 'team_lead'),
            'dobro_kind': event.get_dobro_ru_link_kind_display(),
            'dobro_logged': 'да' if event.dobro_ru_hours_logged else 'нет',
        })
    return rows


def event_report(event_id):
    """Данные для отчёта по одному мероприятию (reports_template.md §4)."""
    event = Event.objects.select_related('organization', 'organizer', 'letter').prefetch_related(
        'shifts__location', 'category_map__category', 'staff__user'
    ).get(pk=event_id)

    categories = ', '.join(m.category.name for m in event.category_map.all()) or '—'
    managers = [st.user for st in event.staff.filter(staff_role='manager')]
    team_leads = [st.user for st in event.staff.filter(staff_role='team_lead')]

    shifts_data = []
    for shift in event.shifts.all().order_by('day', 'start_time'):
        participants = []
        parts = Participation.objects.filter(event_day=shift).select_related('user')
        for p in parts:
            app = (
                Application.objects.filter(event=event, user=p.user)
                .order_by('-created_at').first()
            )
            participants.append({
                'name': p.user.full_name,
                'submitted_at': app.submitted_at if app else None,
                'app_status': app.get_status_display() if app else '—',
                'part_status': p.get_status_display(),
                'hours': p.hours,
                'full_shift': p.full_shift,
            })
        shifts_data.append({
            'day': shift.day,
            'start_time': shift.start_time,
            'end_time': shift.end_time,
            'location': shift.location.name if shift.location else '—',
            'needed': shift.volunteer_needed,
            'participants': participants,
        })

    apps = Application.objects.filter(event=event)
    attended = Participation.objects.filter(event_day__event=event, status='attended')
    needed_total = sum(s.volunteer_needed or 0 for s in event.shifts.all())
    attended_count = attended.count()
    rating = event.feedbacks.aggregate(a=Avg('rating'), c=Count('id'))

    return {
        'event': event,
        'categories': categories,
        'managers': managers,
        'team_leads': team_leads,
        'shifts': shifts_data,
        'totals': {
            'submitted': apps.count(),
            'approved': apps.filter(status='approved').count(),
            'attended': attended_count,
            'needed': needed_total,
            'pct_closed': round(attended_count / needed_total * 100, 1) if needed_total else 0,
            'total_hours': attended.aggregate(s=Sum('hours'))['s'] or 0,
            'people_helped': event.people_helped,
            'rating': round(rating['a'], 1) if rating['a'] is not None else None,
            'rating_count': rating['c'],
        },
    }


def dobro_logging_report(date_from=None, date_to=None, event_ids=None, show_logged=False):
    """Отчёт простановки часов на Добро.ру в двух разрезах (reports_template.md §5,
    reports.md §8). В отчёт попадают attended-участия (hours>0) на меро с
    dobro_ru_link_kind ∈ {shared, unique}; kind='none' исключаются."""
    events = reports_service._events_in_scope(date_from, date_to, event_ids).exclude(
        dobro_ru_link_kind='none'
    ).select_related('dobro_ru_shared_link')

    parts_qs = Participation.objects.filter(
        event_day__event__in=events, status='attended', hours__gt=0,
    ).select_related('user', 'event_day', 'event_day__event', 'event_day__event__dobro_ru_shared_link')
    if not show_logged:
        parts_qs = parts_qs.filter(dobro_ru_logged=False)

    # Разрез «по ссылке»: ключ — (kind, link_url, title)
    by_link = {}
    # Разрез «по человеку»: ключ — user_id
    by_person = {}

    for p in parts_qs:
        event = p.event_day.event
        if event.dobro_ru_link_kind == 'unique':
            link_key = f'event:{event.id}'
            link_url = event.dobro_ru_link or '—'
            link_title = event.title
        else:  # shared
            shared = event.dobro_ru_shared_link
            link_key = f'shared:{shared.id}' if shared else 'shared:none'
            link_url = shared.url if shared else '—'
            link_title = shared.title if shared else 'Общая ссылка (не задана)'

        row = {
            'user': p.user.full_name,
            'user_id': p.user_id,
            'event': event.title,
            'day': p.event_day.day,
            'hours': p.hours,
            'logged': p.dobro_ru_logged,
            'participation_id': p.id,
            'link_kind': event.dobro_ru_link_kind,
            'link_url': link_url,
            'link_title': link_title,
        }

        link = by_link.setdefault(link_key, {
            'title': link_title, 'url': link_url, 'kind': event.dobro_ru_link_kind,
            'rows': [], 'total_hours': 0, 'logged_count': 0,
        })
        link['rows'].append(row)
        link['total_hours'] += p.hours
        if p.dobro_ru_logged:
            link['logged_count'] += 1

        person = by_person.setdefault(p.user_id, {
            'name': p.user.full_name, 'telegram': p.user.telegram_username or '',
            'rows': [], 'total_hours': 0,
        })
        person['rows'].append(row)
        person['total_hours'] += p.hours

    excluded_none = reports_service._events_in_scope(date_from, date_to, event_ids).filter(
        dobro_ru_link_kind='none'
    ).count()

    all_parts = Participation.objects.filter(
        event_day__event__in=events, status='attended', hours__gt=0,
    )
    return {
        'by_link': list(by_link.values()),
        'by_person': list(by_person.values()),
        'summary': {
            'to_log': all_parts.filter(dobro_ru_logged=False).count(),
            'logged': all_parts.filter(dobro_ru_logged=True).count(),
            'excluded_none': excluded_none,
        },
    }


# ---------------------------------------------------------------------------
# Сборка Excel-книг (reports_template.md §2, §4, §5)
# ---------------------------------------------------------------------------

def build_period_workbook(date_from, date_to, event_ids, scope_mode, compare_mode):
    """Полная книга отчёта за охват (reports_template.md §2): агрегатные листы
    (дублируют внутренний PDF) + сырые построчные листы «Заявки» и «Мероприятия»."""
    from openpyxl import Workbook

    prev_from = prev_to = None
    if scope_mode == 'period':
        prev_from, prev_to = reports_service.previous_period_range(date_from, date_to, compare_mode)

    data = reports_service.period_report(date_from, date_to, prev_from, prev_to, event_ids=event_ids)
    efficiency = reports_service.recruitment_efficiency(date_from, date_to, event_ids=event_ids)
    staff = reports_service.staff_load(date_from, date_to, event_ids=event_ids)
    timing = reports_service.application_timing(date_from, date_to, event_ids=event_ids)
    post_timing = reports_service.post_publication_timing(date_from, date_to, event_ids=event_ids)
    weekly = reports_service.weekly_dynamics(date_from, date_to, event_ids=event_ids)

    scope_label = (
        f'{date_from} — {date_to}' if scope_mode == 'period'
        else f'{len(event_ids)} выбранных мероприятий'
    )

    wb = Workbook()

    ws = wb.active
    ws.title = 'Обзор'
    ws.append(['Отчёт за охват', scope_label])
    ws.append([])
    ws.append(['Показатель', 'Значение'])
    ws.append(['Всего часов', data['total_hours']])
    ws.append(['Всего мероприятий', data['total_events']])
    ws.append(['Уникальных волонтёров', data['unique_volunteers']])
    ws.append(['Закрытых смен', data['closed_shifts']])
    ws.append(['Новых волонтёров', data['new_volunteers']])
    ws.append(['Людей получили помощь', data['people_helped']])

    ws_top = wb.create_sheet('Топы')
    ws_top.append(['Тип', 'Позиция', 'Имя/категория', 'Значение'])
    for i, v in enumerate(data['top_volunteers'], start=1):
        ws_top.append(['Волонтёр', i, v['name'], v['hours']])
    for i, c in enumerate(data['top_categories'], start=1):
        ws_top.append(['Категория', i, c['event_day__event__category_map__category__name'] or '—', c['count']])

    ws_cat = wb.create_sheet('Распределение по категориям')
    ws_cat.append(['Категория', 'Волонтёров', 'Часов', 'Мероприятий'])
    for c in data['category_breakdown']:
        ws_cat.append([c['name'] or 'Без категории', c['volunteers'], c['hours'] or 0, c['events']])

    ws_funnel = wb.create_sheet('Воронка')
    ws_funnel.append(['Этап', 'Значение'])
    ws_funnel.append(['Подано заявок', data['funnel']['submitted']])
    ws_funnel.append(['Одобрено', data['funnel']['approved']])
    ws_funnel.append(['Отклонено (резерв)', data['funnel']['rejected']])
    ws_funnel.append(['Явка', data['funnel']['attended']])
    ws_funnel.append(['Неявка без предупреждения', data['funnel']['unexcused']])
    ws_funnel.append(['Конверсия подал → явка, %', data['funnel']['conversion_pct']])

    ws_eff = wb.create_sheet('Эффективность набора')
    ws_eff.append(['Мероприятие', 'Смена (день)', 'Нужно', 'Пришло', '% закрытия'])
    for s in efficiency['shifts']:
        ws_eff.append([s['title'], s['day'].isoformat(), s['needed'], s['attended'], s['pct_closed']])
    ws_eff.append([])
    ws_eff.append(['Среднее время набора (дн.)', efficiency['avg_recruit_days'] if efficiency['avg_recruit_days'] is not None else '—'])

    ws_staff = wb.create_sheet('Нагрузка персонала')
    ws_staff.append(['Роль', 'ФИО', 'Мероприятий', 'Заявок обработано', 'Ср. время обработки, ч'])
    for m in staff['managers']:
        ws_staff.append(['Менеджер', f"{m['user__first_name'] or ''} {m['user__last_name'] or ''}".strip(),
                         m['event_count'], m['applications_processed'], m['avg_processing_hours']])
    for t in staff['team_leads']:
        ws_staff.append(['Тим-лидер', f"{t['user__first_name'] or ''} {t['user__last_name'] or ''}".strip(),
                         t['event_count'], t['applications_processed'], t['avg_processing_hours']])

    ws_ret = wb.create_sheet('Retention Churn')
    ws_ret.append(['Показатель', 'Значение'])
    ws_ret.append(['Churn rate, %', data['retention']['churn_rate_pct']])
    ws_ret.append(['Новых в blacklist', data['retention']['new_blacklisted']])

    ws_problems = wb.create_sheet('Проблемные зоны')
    ws_problems.append(['Категория', 'Мероприятие/волонтёр', 'Метрика'])
    for e in data['problem_zones']['low_rated_events']:
        ws_problems.append(['Низкий рейтинг', e['title'], f"{e['avg_rating']:.1f} ({e['fb_count']} отзывов)"])
    for u in data['problem_zones']['at_risk_users']:
        name = f"{u['first_name'] or ''} {u['last_name'] or ''}".strip() or u['telegram_username'] or '—'
        ws_problems.append(['На грани blacklist', name, round(u['churn_score'], 2)])

    if data['comparison']:
        ws_cmp = wb.create_sheet('Сравнение')
        cmp = data['comparison']
        ws_cmp.append([f"База: {cmp['date_from']} — {cmp['date_to']} ({'год назад' if compare_mode == 'year_ago' else 'смежный период'})"])
        ws_cmp.append(['Метрика', 'Δ абс.', 'Δ %'])
        ws_cmp.append(['Часы', cmp['hours_delta'], cmp['hours_delta_pct']])
        ws_cmp.append(['Мероприятия', cmp['events_delta'], cmp['events_delta_pct']])
        ws_cmp.append(['Уникальных волонтёров', cmp['volunteers_delta'], cmp['volunteers_delta_pct']])
        ws_cmp.append(['Новых волонтёров', cmp['new_volunteers_delta'], cmp['new_volunteers_delta_pct']])

    ws_charts = wb.create_sheet('Данные для графиков')
    ws_charts.append(['График', 'Ключ', 'Значение'])
    for d in timing['density']:
        ws_charts.append(['Отклик на меро (плотность)', d['label'], d['count']])
    for hour, count in enumerate(post_timing['by_hour']):
        ws_charts.append(['Публикация постов (час)', f'{hour}:00', count])
    for b in data['churn_histogram']:
        ws_charts.append(['Распределение churn', b['label'], b['count']])
    for w in weekly:
        ws_charts.append(['Динамика по неделям', f"{w['from']}–{w['to']}", f"{w['hours']} ч / {w['shifts']} смен"])

    # --- Сырые построчные листы (§2.1, §2.2) ---
    ws_apps = wb.create_sheet('Заявки')
    ws_apps.append([
        'ID', 'Волонтёр', 'Telegram', 'Телефон', 'user_id', 'Мероприятие', 'Смена',
        'Функция', 'Роль', 'Когда подал', 'Загружено', 'Статус', 'Кто обработал',
        'Когда обработано', 'Пришёл?', 'Полная смена', 'Часы',
    ])
    for r in applications_rows(date_from, date_to, event_ids):
        ws_apps.append([
            r['id'], r['name'], r['telegram'], r['phone'], r['user_id'], r['event'],
            r['shift_day'].isoformat() if r['shift_day'] else '—',
            r['functions'], r['role'], _fmt_dt(r['submitted_at']), _fmt_dt(r['created_at']),
            r['status'], r['assigned_by'], _fmt_dt(r['assigned_at']),
            r['attended'], r['full_shift'], r['hours'],
        ])

    ws_ev = wb.create_sheet('Мероприятия')
    ws_ev.append([
        'ID', 'Название', 'Организация', 'Организатор', 'Категории', 'Дата с', 'Дата по',
        'Смен', 'Локации', 'Статус', 'Нужно', 'Подано', 'Одобрено', 'Явка', '% закрытия',
        'Часов', 'Людей помогли', 'Рейтинг', 'Отзывов', 'Менеджеры', 'Тим-лидеры',
        'Учёт Добро.ру', 'Часы проставлены',
    ])
    for r in events_rows(date_from, date_to, event_ids):
        ws_ev.append([
            r['id'], r['title'], r['organization'], r['organizer'], r['categories'],
            r['date_from'].isoformat() if r['date_from'] else '—',
            r['date_to'].isoformat() if r['date_to'] else '—',
            r['shift_count'], r['locations'], r['event_status'], r['needed'], r['submitted'],
            r['approved'], r['attended'], r['pct_closed'], r['total_hours'], r['people_helped'],
            r['rating'], r['rating_count'], r['managers'], r['team_leads'],
            r['dobro_kind'], r['dobro_logged'],
        ])

    return wb


def build_event_workbook(event_id):
    """Книга отчёта по одному мероприятию (reports_template.md §4 Excel-версия)."""
    from openpyxl import Workbook

    rep = event_report(event_id)
    event = rep['event']
    wb = Workbook()

    ws = wb.active
    ws.title = 'Шапка'
    ws.append(['Отчёт по мероприятию', event.title])
    ws.append([])
    ws.append(['Организация', rep['event'].organization.name if event.organization else '—'])
    ws.append(['Организатор', f"{event.organizer.first_name} {event.organizer.last_name}" if event.organizer else '—'])
    ws.append(['Категории', rep['categories']])
    ws.append(['Локация', ', '.join(sorted({s['location'] for s in rep['shifts']}))])
    ws.append(['Статус', event.get_event_status_display()])
    ws.append(['Ссылка на чат', event.chat_link or '—'])
    ws.append(['Пост в канале', event.post_in_chanel_link or '—'])
    ws.append(['Добро.ру', event.dobro_ru_link or '—'])
    ws.append(['Менеджеры', ', '.join(u.full_name for u in rep['managers']) or '—'])
    ws.append(['Тим-лидеры', ', '.join(u.full_name for u in rep['team_leads']) or '—'])

    ws_sh = wb.create_sheet('Смены и участники')
    ws_sh.append(['Смена (день)', 'Волонтёр', 'Когда подал', 'Статус заявки', 'Статус участия', 'Полная смена', 'Часы'])
    for shift in rep['shifts']:
        if not shift['participants']:
            ws_sh.append([shift['day'].isoformat(), '— нет участников —', '', '', '', '', ''])
        for p in shift['participants']:
            ws_sh.append([
                shift['day'].isoformat(), p['name'], _fmt_dt(p['submitted_at']),
                p['app_status'], p['part_status'], 'да' if p['full_shift'] else 'нет', p['hours'],
            ])

    ws_t = wb.create_sheet('Итоги')
    t = rep['totals']
    ws_t.append(['Показатель', 'Значение'])
    ws_t.append(['Всего заявок', t['submitted']])
    ws_t.append(['Одобрено', t['approved']])
    ws_t.append(['Явка', t['attended']])
    ws_t.append(['Нужно волонтёров', t['needed']])
    ws_t.append(['% закрытия', t['pct_closed']])
    ws_t.append(['Всего часов', t['total_hours']])
    ws_t.append(['Людей получили помощь', t['people_helped'] if t['people_helped'] is not None else '—'])
    ws_t.append(['Средний рейтинг', f"{t['rating']} ({t['rating_count']} отзывов)" if t['rating'] is not None else '—'])
    ws_t.append(['Внутренние комментарии', event.internal_comment or '—'])

    return wb


def build_dobro_workbook(date_from, date_to, event_ids, show_logged=False):
    """Книга простановки часов на Добро.ру в двух разрезах + сводка
    (reports_template.md §5)."""
    from openpyxl import Workbook

    rep = dobro_logging_report(date_from, date_to, event_ids, show_logged)
    wb = Workbook()

    ws_sum = wb.active
    ws_sum.title = 'Сводка'
    ws_sum.append(['Показатель', 'Значение'])
    ws_sum.append(['Участий к простановке', rep['summary']['to_log']])
    ws_sum.append(['Уже проставлено', rep['summary']['logged']])
    ws_sum.append(['Меро исключено (нет ссылки)', rep['summary']['excluded_none']])

    ws_link = wb.create_sheet('По ссылке')
    for link in rep['by_link']:
        ws_link.append([
            f"🔗 {link['title']}", link['url'], f"[{link['kind']}]",
            f"проставлено {link['logged_count']}/{len(link['rows'])}", f"{link['total_hours']} ч",
        ])
        ws_link.append(['Волонтёр', 'Мероприятие · день', 'Часы', 'Проставлено'])
        for r in link['rows']:
            ws_link.append([r['user'], f"{r['event']} · {r['day'].isoformat()}", r['hours'], 'да' if r['logged'] else 'нет'])
        ws_link.append([])

    ws_person = wb.create_sheet('По человеку')
    for person in rep['by_person']:
        ws_person.append([
            f"{person['name']} ({person['telegram'] or '—'})",
            f"{len(person['rows'])} участий", f"{person['total_hours']} ч",
        ])
        ws_person.append(['Ссылка', 'Мероприятие · день', 'Часы', 'Проставлено'])
        for r in person['rows']:
            ws_person.append([r['link_url'], f"{r['event']} · {r['day'].isoformat()}", r['hours'], 'да' if r['logged'] else 'нет'])
        ws_person.append([])

    return wb
