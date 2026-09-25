"""Обработка заявок: матчинг с профилем, approve/reject, авто-создание participations."""
import datetime

from django.db import transaction
from django.utils import timezone

from blog.models import ApplicationShiftDecision, User, Application, Event, Participation


def application_shifts(application: Application):
    """Смены заявки: выбранные чипами, либо все смены меро (Excel-импорт/бот)."""
    return list(application.selected_shifts.all()) or list(application.event.shifts.all())


def ensure_shift_decisions(application: Application):
    """Гарантирует, что у заявки есть ApplicationShiftDecision на каждую её смену
    (создаёт недостающие со статусом 'new') — вызывается лениво при первом обращении
    к разбивке по сменам, чтобы не плодить миграции данных при каждом новом поле."""
    shifts = application_shifts(application)
    existing_shift_ids = set(application.shift_decisions.values_list('shift_id', flat=True))
    for shift in shifts:
        if shift.id not in existing_shift_ids:
            ApplicationShiftDecision.objects.get_or_create(
                application=application, shift=shift, defaults={'status': application.status},
            )
    return application.shift_decisions.select_related('shift').all()


def _recompute_application_status(application: Application):
    """Статус заявки в целом — производный от решений по сменам: 'new', пока хоть
    одна смена не решена; 'approved', если все решённые смены одобрены; иначе
    'rejected'. Нужен только для сводных списков (напр. „Необработанные заявки“),
    реальный источник истины — ApplicationShiftDecision по каждой смене."""
    statuses = set(application.shift_decisions.values_list('status', flat=True))
    if not statuses or 'new' in statuses:
        new_status = 'new'
    elif statuses == {'approved'}:
        new_status = 'approved'
    else:
        new_status = 'rejected'
    if application.status != new_status:
        application.status = new_status
        application.save(update_fields=['status'])


def find_matching_user(telegram_username=None, phone=None, first_name=None, last_name=None):
    """Матчинг заявки с профилем (db.md, бизнес-правило 5):
    (1) telegram_username -> (2) phone -> (3) ФИО."""
    if telegram_username:
        user = User.objects.filter(telegram_username__iexact=telegram_username.lstrip('@')).first()
        if user:
            return user
    if phone:
        user = User.objects.filter(phone=phone).first()
        if user:
            return user
    if first_name and last_name:
        user = User.objects.filter(first_name__iexact=first_name, last_name__iexact=last_name).first()
        if user:
            return user
    return None


@transaction.atomic
def process_application(application: Application, new_status: str, processed_by: User):
    """approve/reject заявки. При approve — авто-создание participations по сменам,
    выбранным в заявке (`selected_shifts`, чипы на веб-форме — правило 4). Если смены
    не выбраны (заявка из Excel-импорта, там выбора нет) — на все смены мероприятия."""
    application.status = new_status
    application.assigned_at = timezone.now()
    application.assigned_by = processed_by
    application.save(update_fields=['status', 'assigned_at', 'assigned_by'])

    if new_status == 'approved' and application.user:
        shifts = application.selected_shifts.all() or application.event.shifts.all()
        for shift in shifts:
            duration_hours = max(0, round((shift.end_time - shift.start_time).total_seconds() / 3600))
            Participation.objects.get_or_create(
                user=application.user,
                event_day=shift,
                # По умолчанию считаем, что волонтёр пришёл и отработал полную смену —
                # часы сразу равны длительности смены, без ручного "перетыкивания" (см.
                # events.md: тим-лидер меняет статус только если по факту было иначе).
                defaults={'status': 'attended', 'hours': duration_hours, 'full_shift': True},
            )
        # Approve создаёт participations сразу со status='attended' и часами — кэш
        # (total_hours/total_event_count/season_*) обязан пересчитаться тут же, а не
        # только при последующей ручной простановке явки, иначе статистика волонтёра
        # "зависает" на нулях до первого вмешательства тим-лидера.
        from blog.services.stats import recalc_user_aggregates
        recalc_user_aggregates(application.user)

    # Автоматика только ЗАКРЫВАЕТ набор на смену при достижении порога (правило 10) —
    # никогда не открывает его обратно. Открытие/закрытие вручную (кнопка «Набор закрыт»
    # на смене) не должно откатываться следующей обработкой заявки.
    from blog.services.stats import recruit_status_for_shift
    for shift in application.event.shifts.filter(recruit_status='open'):
        if recruit_status_for_shift(shift) == 'closed':
            shift.recruit_status = 'closed'
            shift.save(update_fields=['recruit_status'])

    from blog.services.notifications import notify_application_status
    notify_application_status(application)

    return application


@transaction.atomic
def process_shift_decision(application: Application, shift, new_status: str, processed_by: User):
    """approve/reject заявки ТОЛЬКО на конкретную смену — остальные смены той же
    заявки (если она подана на несколько) не затрагиваются. Источник истины —
    ApplicationShiftDecision; Application.status пересчитывается как производный
    агрегат для сводных списков (см. _recompute_application_status)."""
    ensure_shift_decisions(application)
    decision, _ = ApplicationShiftDecision.objects.get_or_create(application=application, shift=shift)
    decision.status = new_status
    decision.assigned_at = timezone.now()
    decision.assigned_by = processed_by
    decision.save(update_fields=['status', 'assigned_at', 'assigned_by'])

    if application.assigned_at is None:
        application.assigned_at = timezone.now()
        application.assigned_by = processed_by
        application.save(update_fields=['assigned_at', 'assigned_by'])

    if new_status == 'approved' and application.user:
        duration_hours = max(0, round((shift.end_time - shift.start_time).total_seconds() / 3600))
        Participation.objects.get_or_create(
            user=application.user,
            event_day=shift,
            defaults={'status': 'attended', 'hours': duration_hours, 'full_shift': True},
        )
        from blog.services.stats import recalc_user_aggregates
        recalc_user_aggregates(application.user)

    _recompute_application_status(application)

    from blog.services.stats import recruit_status_for_shift
    if shift.recruit_status == 'open' and recruit_status_for_shift(shift) == 'closed':
        shift.recruit_status = 'closed'
        shift.save(update_fields=['recruit_status'])

    from blog.services.notifications import notify_shift_decision_status
    notify_shift_decision_status(application, shift, new_status)

    return decision


def application_earliest_shift(application: Application):
    """Ближайшая по времени смена заявки (выбранные чипами, либо все смены меро,
    если выбор не делался — заявки из Excel-импорта/бота)."""
    shifts = application.selected_shifts.all() or application.event.shifts.all()
    starts = [s.start_time for s in shifts]
    return min(starts) if starts else None


def can_cancel_application(application: Application) -> tuple:
    """Можно ли отменить заявку самим волонтёром (db.md/tg-bot.md): только пока статус
    активен (`new`/`approved`) и до ближайшей смены осталось больше `const.late_cancel_hours`.

    Возвращает (можно: bool, причина отказа: str|None, late_cancel_hours: float).
    """
    from data_base.data_controller import get_const

    late_cancel_hours = get_const('late_cancel_hours', default=24)
    if application.status not in ('new', 'approved'):
        return False, 'inactive', late_cancel_hours

    earliest_start = application_earliest_shift(application)
    too_late = bool(
        earliest_start and earliest_start - timezone.now() < datetime.timedelta(hours=late_cancel_hours)
    )
    if too_late:
        return False, 'too_late', late_cancel_hours
    return True, None, late_cancel_hours


@transaction.atomic
def cancel_application(application: Application, reason: str) -> Application:
    """Отмена заявки самим волонтёром — причина обязательна. Вызывающий обязан
    предварительно проверить `can_cancel_application`. Событие попадает в ленту
    изменений заявок мероприятия (ApplicationChangeLog), чтобы тим-лидер увидел
    отказ на странице меро."""
    application.status = 'cancelled'
    application.cancelled_at = timezone.now()
    application.cancel_reason = reason
    application.save(update_fields=['status', 'cancelled_at', 'cancel_reason'])

    from blog.models import ApplicationChangeLog
    name = f'{application.first_name} {application.last_name or ""}'.strip()
    ApplicationChangeLog.objects.create(
        event=application.event,
        application=application,
        kind='withdrawn',
        text=f'{name} отказался(-ась) от участия. Причина: {reason}',
    )
    return application


def event_closing_checklist(event: Event) -> dict:
    """Список условий для завершения мероприятия (cjm-event-lifecycle.md, этап 8).

    Возвращает словарь с булевыми флагами по каждому условию и человекочитаемыми
    пояснениями — используется и для показа кнопки «Завершить», и на экране проверки.
    """
    today = timezone.now().date()

    shifts = list(event.shifts.all())
    all_days_past = bool(shifts) and all(s.day < today for s in shifts)

    new_applications = event.applications.filter(status='new')
    no_pending_applications = not new_applications.exists()

    # Заявки, одобренные, но не привязанные к профилю пользователя — по ним
    # невозможно проставить явку (Participation привязан к User), поэтому
    # завершение мероприятия ими тоже блокируется — их нужно либо привязать
    # к профилю (или переимпортировать), либо перевести в резерв.
    approved_without_user = event.applications.filter(status='approved', user__isnull=True)
    no_approved_without_user = not approved_without_user.exists()

    people_helped_filled = event.people_helped is not None

    ready = (
        event.event_status != 'finished'
        and all_days_past
        and no_pending_applications
        and no_approved_without_user
    )

    return {
        'ready': ready,
        'already_finished': event.event_status == 'finished',
        'all_days_past': all_days_past,
        'has_days': bool(shifts),
        'no_pending_applications': no_pending_applications,
        'pending_applications_count': new_applications.count(),
        'no_approved_without_user': no_approved_without_user,
        'approved_without_user_count': approved_without_user.count(),
        'people_helped_filled': people_helped_filled,
    }
