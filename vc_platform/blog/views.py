import datetime
import random
from urllib.parse import quote

from django.contrib import messages
from django.contrib.auth import authenticate, login as auth_login, logout as auth_logout
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Avg, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from blog.decorators import manager_required
from blog.forms import (
    ApplicationCancelForm, ApplicationForm, ConstForm, EventCommentForm, EventShiftForm, EventFeedbackForm, EventForm,
    ExcelImportForm, ExternalExperienceForm, LocationForm, LoginForm, OrganizationForm, OrganizerForm, ProfileCompletionForm,
    ParticipationForm, QuickCategoryForm, QuickLocationForm, QuickOrganizationForm, QuickOrganizerForm,
    RoleUsersForm, SeasonForm, UserCommentForm,
)
from blog.models import (
    Application, ApplicationShiftDecision, Const, Event, EventCategory, EventComment, EventShift, EventFeedback, EventStaff,
    Location, Manager, Organization, Organizer, Participation, Season, TeamLeader, User,
)
from blog.services import import_applications as import_service
from blog.services import notifications as notifications_service
from blog.services import reports as reports_service
from blog.services import report_exports
from blog.services.applications import (
    can_cancel_application, cancel_application, event_closing_checklist, process_application,
)
from blog.services import roles as roles_service
from blog.services.roles import (
    account_is_complete, can_process_applications, has_extended_access_to_profile, is_manager, is_team_leader, role_label,
)
from blog.services.stats import recalc_user_aggregates


# ---------------------------------------------------------------------------
# AJAX-модалка/bottom sheet: общий хелпер для форм создания/редактирования
# ---------------------------------------------------------------------------

def is_ajax(request) -> bool:
    return request.headers.get('X-Requested-With') == 'XMLHttpRequest'


def modal_form_response(request, template_name, context, *, success_redirect=None, partial_template=None):
    """Рендерит форму для AJAX-модалки/bottom sheet (`base.html`, `modal.js`).

    - AJAX + успешный POST → JSON {"redirect": url} — модалка закрывается, страница переходит.
    - AJAX + GET/невалидный POST → только partial формы (без сайдбара/base.html).
    - Не-AJAX → обычное поведение (полная страница/redirect), как без JS.

    `partial_template` — кастомный partial для форм с доп. разметкой (напр. quick-add панели
    в event_form); по умолчанию используется общий `_modal_form.html`.
    """
    if success_redirect is not None:
        if is_ajax(request):
            return JsonResponse({'redirect': success_redirect})
        return redirect(success_redirect)

    if is_ajax(request):
        return render(request, partial_template or 'blog/partials/_modal_form.html', context)
    return render(request, template_name, context)


# ---------------------------------------------------------------------------
# Быстрое создание справочников прямо из формы мероприятия (без ухода со страницы)
# ---------------------------------------------------------------------------

@manager_required
def quick_create_location(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'Метод не поддерживается.'}, status=405)
    form = QuickLocationForm(request.POST)
    if form.is_valid():
        location = form.save()
        return JsonResponse({'id': location.id, 'label': location.name})
    return JsonResponse({'errors': form.errors}, status=400)


@manager_required
def quick_create_organization(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'Метод не поддерживается.'}, status=405)
    form = QuickOrganizationForm(request.POST)
    if form.is_valid():
        organization = form.save()
        return JsonResponse({'id': organization.id, 'label': organization.name})
    return JsonResponse({'errors': form.errors}, status=400)


@manager_required
def quick_create_organizer(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'Метод не поддерживается.'}, status=405)
    form = QuickOrganizerForm(request.POST)
    if form.is_valid():
        organizer = form.save()
        label = f'{organizer.first_name} {organizer.last_name}'.strip()
        return JsonResponse({'id': organizer.id, 'label': label})
    return JsonResponse({'errors': form.errors}, status=400)


@manager_required
def quick_create_category(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'Метод не поддерживается.'}, status=405)
    form = QuickCategoryForm(request.POST)
    if form.is_valid():
        category = form.save()
        return JsonResponse({'id': category.id, 'label': category.name})
    return JsonResponse({'errors': form.errors}, status=400)


# ---------------------------------------------------------------------------
# Аутентификация
# ---------------------------------------------------------------------------

def _safe_next(request, default='dashboard'):
    """Возвращает безопасный URL для редиректа после входа: только относительный
    путь того же сайта (защита от open-redirect), иначе — дефолтный маршрут."""
    from django.utils.http import url_has_allowed_host_and_scheme

    next_url = request.POST.get('next') or request.GET.get('next')
    if next_url and url_has_allowed_host_and_scheme(
        next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return next_url
    return reverse(default)


def _post_tg_login_redirect(request, user):
    """Куда отправить пользователя сразу после входа через Telegram. Если аккаунт
    ещё не «заполнен» (нет ФИО) — на дозаполнение профиля, сохранив исходный next,
    чтобы после заполнения вернуть человека туда, куда он шёл (напр. подача заявки)."""
    target = _safe_next(request)
    if not account_is_complete(user):
        return reverse('complete_profile') + f'?next={quote(target)}'
    return target


def login_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard')

    from django.conf import settings

    form = LoginForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        user = authenticate(
            request,
            login=form.cleaned_data['login'],
            password=form.cleaned_data['password'],
        )
        if user is not None:
            auth_login(request, user)
            return redirect(_safe_next(request))
        messages.error(request, 'Неверный логин или пароль.')

    return render(request, 'blog/login.html', {
        'form': form,
        'next': request.GET.get('next', ''),
        'tg_bot_username': settings.TG_BOT_USERNAME,
        # Абсолютный URL коллбэка Login Widget — Telegram шлёт данные на data-auth-url.
        'tg_widget_auth_url': request.build_absolute_uri(reverse('telegram_login_callback')),
    })


@csrf_exempt
def telegram_login_callback(request):
    """Коллбэк Telegram Login Widget (браузерный вход вне Mini App): виджет
    редиректит сюда с подписанными полями пользователя. Проверяем HMAC и логиним.

    `csrf_exempt` по тем же причинам, что и `webapp_auth`: подлинность подтверждается
    подписью Telegram, а не CSRF-токеном (у клиента ещё нет сессии)."""
    from blog.services.telegram_auth import validate_login_widget
    from data_base import data_controller as dc

    data = {k: v for k, v in request.GET.items() if k != 'next'}
    payload = validate_login_widget(data)
    if not payload or not payload.get('id'):
        messages.error(request, 'Не удалось подтвердить вход через Telegram. Попробуйте ещё раз.')
        return redirect('login')

    user, _ = dc.get_or_create_user_by_telegram_id(payload['id'], payload.get('username'))
    if user.status == 'blacklisted':
        messages.error(request, 'Доступ ограничен — обратитесь к менеджеру центра.')
        return redirect('login')

    auth_login(request, user, backend='blog.auth_backends.TelegramWebAppBackend')
    return redirect(_post_tg_login_redirect(request, user))


@login_required
def complete_profile(request):
    """Дозаполнение аккаунта после входа через Telegram: ФИО обязательно, телефон/
    почта/дата рождения — по желанию. Без «заполненного аккаунта» (ФИО + Telegram)
    нельзя подать заявку — сюда редиректит вход и попытка подачи заявки."""
    if account_is_complete(request.user) and request.method == 'GET' and not request.GET.get('edit'):
        return redirect(_safe_next(request))

    form = ProfileCompletionForm(request.POST or None, instance=request.user)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Профиль заполнен — теперь можно подавать заявки.')
        return redirect(_safe_next(request))

    return render(request, 'blog/complete_profile.html', {
        'form': form,
        'next': request.GET.get('next', ''),
    })


def tg_link_login(request, token):
    """Вход по одноразовой ссылке из бота (deep-link `?start=weblogin`): проверяем
    токен (не использован и не старше 5 минут), логиним и гасим токен."""
    from blog.services.web_auth import consume_auth_token

    user = consume_auth_token(str(token))
    if user is None:
        messages.error(request, 'Ссылка для входа недействительна или устарела. Запросите новую в боте.')
        return redirect('login')
    if user.status == 'blacklisted':
        messages.error(request, 'Доступ ограничен — обратитесь к менеджеру центра.')
        return redirect('login')

    auth_login(request, user, backend='blog.auth_backends.TelegramWebAppBackend')
    return redirect(_post_tg_login_redirect(request, user))


def logout_view(request):
    auth_logout(request)
    return redirect('login')


# ---------------------------------------------------------------------------
# Публичный (гостевой) контур — просмотр опубликованных мероприятий без входа
# и публичная страница волонтёрского опыта (раздел 2 и 4).
# ---------------------------------------------------------------------------

def _published_active_events():
    """Опубликованные и ещё не завершённые мероприятия (для гостевого списка и
    списка активных). Сортировка — ближайшие предстоящие/идущие сверху."""
    events = list(
        Event.objects.filter(published_at__isnull=False)
        .exclude(event_status='finished')
        .select_related('location')
        .prefetch_related('shifts')
    )
    rows = []
    for event in events:
        if event.compute_status() in ('passed', 'finished'):
            continue
        days = sorted(s.day for s in event.shifts.all())
        event.first_day = days[0] if days else None
        event.last_day = days[-1] if days else None
        rows.append(event)
    rows.sort(key=lambda e: (e.first_day is None, e.first_day or datetime.date.max))
    return rows


def public_event_list(request):
    """Гостевой список идущих/предстоящих мероприятий — без входа. Кнопка «Подать
    заявку» на карточке ведёт на вход (заявку подаёт только авторизованный)."""
    return render(request, 'blog/public_event_list.html', {
        'events': _published_active_events(),
        'is_public': True,
    })


def public_event_detail(request, token):
    """Публичная витрина мероприятия по ссылке «Поделиться» — read-only, без входа.
    Доступна только для опубликованного меро."""
    event = get_object_or_404(
        Event.objects.select_related('location', 'organization'),
        public_token=token, published_at__isnull=False,
    )
    shifts = list(event.shifts.select_related('location').order_by('day'))
    return render(request, 'blog/public_event_detail.html', {
        'event': event,
        'shifts': shifts,
        'is_public': True,
        # После входа вернуть человека на подачу заявки именно на это меро.
        'apply_next': reverse('application_create') + f'?event={event.id}',
    })


def public_experience(request, token):
    """Публичная страница волонтёрского опыта по ссылке «Скопировать опыт» — без
    входа, показывает только блок опыта (без ПДн/комментариев)."""
    from blog.services.experience import build_experience

    target = get_object_or_404(User, experience_token=token)
    return render(request, 'blog/public_experience.html', {
        'target': target,
        'experience': build_experience(target),
        'is_public': True,
    })


# ---------------------------------------------------------------------------
# Внешний опыт волонтёра (раздел «Мой опыт») — CRUD только для владельца профиля
# ---------------------------------------------------------------------------

@login_required
def external_experience_add(request):
    form = ExternalExperienceForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        exp = form.save(commit=False)
        exp.user = request.user
        exp.save()
        messages.success(request, 'Внешнее мероприятие добавлено в ваш опыт.')
        return modal_form_response(
            request, None, None,
            success_redirect=reverse('profile_detail', args=[request.user.id]) + '#experience',
        )
    return modal_form_response(
        request, 'blog/external_experience_form.html',
        {'form': form, 'title': 'Добавить внешнее мероприятие', 'submit_label': 'Добавить'},
    )


@login_required
def external_experience_edit(request, exp_id):
    from blog.models import ExternalExperience
    exp = get_object_or_404(ExternalExperience, pk=exp_id, user=request.user)
    form = ExternalExperienceForm(request.POST or None, instance=exp)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Запись обновлена.')
        return modal_form_response(
            request, None, None,
            success_redirect=reverse('profile_detail', args=[request.user.id]) + '#experience',
        )
    return modal_form_response(
        request, 'blog/external_experience_form.html',
        {'form': form, 'title': 'Редактировать внешнее мероприятие', 'submit_label': 'Сохранить'},
    )


@login_required
def external_experience_delete(request, exp_id):
    from blog.models import ExternalExperience
    exp = get_object_or_404(ExternalExperience, pk=exp_id, user=request.user)
    if request.method == 'POST':
        exp.delete()
        messages.success(request, 'Запись удалена.')
    return redirect(reverse('profile_detail', args=[request.user.id]) + '#experience')


# ---------------------------------------------------------------------------
# TG Mini App — вход через Telegram initData вместо логина/пароля
# (architecture.md §2.1, db.md правило 8): один и тот же Django-веб, но пользователя
# логинит не форма, а подпись Telegram, проверенная на бэкенде. Дальше используется
# обычная Django-сессия — весь остальной код (шаблоны, @login_required, ACL) не меняется.
# ---------------------------------------------------------------------------

def webapp_entry(request):
    """Страница-лоадер, открываемая кнопкой Mini App в боте: достаёт `initData` из
    Telegram WebApp JS SDK на клиенте и отправляет на `webapp_auth`.

    Единственная страница, которой разрешено встраиваться в `<iframe>` (десктопный
    Telegram-клиент открывает Mini App именно так) — остальной сайт по-прежнему
    защищён от clickjacking через глобальный `XFrameOptionsMiddleware` (DENY).
    """
    response = render(request, 'blog/webapp_entry.html')
    response['X-Frame-Options'] = 'ALLOWALL'
    return response


@csrf_exempt
def webapp_auth(request):
    """Проверяет `initData`, логинит (или создаёт как гостя) пользователя по
    `telegram_id` через обычную Django-сессию — без пароля.

    `csrf_exempt` обоснован: подлинность запроса здесь подтверждается HMAC-подписью
    Telegram (`validate_init_data`), а не CSRF-токеном — на этот момент у клиента ещё
    нет сессии/csrf-cookie (это первый запрос Mini App), так что обычная CSRF-защита
    в принципе неприменима, как и для вебхуков внешних сервисов.
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'Метод не поддерживается.'}, status=405)

    from blog.services.telegram_auth import validate_init_data
    from data_base import data_controller as dc

    init_data = request.POST.get('init_data', '')
    payload = validate_init_data(init_data)
    if not payload or not payload.get('user') or not payload['user'].get('id'):
        return JsonResponse({'error': 'Не удалось подтвердить данные Telegram.'}, status=400)

    tg_user = payload['user']
    user, _ = dc.get_or_create_user_by_telegram_id(
        tg_user['id'], tg_user.get('username')
    )
    if user.status == 'blacklisted':
        return JsonResponse({'error': 'Доступ ограничен — обратитесь к менеджеру центра.'}, status=403)

    auth_login(request, user, backend='blog.auth_backends.TelegramWebAppBackend')
    # Флаг в сессии: запросы из Mini App разрешено встраивать в <iframe> (десктопный
    # Telegram-клиент), обычный веб-вход по логину/паролю — по-прежнему DENY против
    # clickjacking. Читается в TelegramWebAppFrameMiddleware.
    request.session['is_webapp_session'] = True
    return JsonResponse({'redirect': _post_tg_login_redirect(request, user)})


# ---------------------------------------------------------------------------
# Дашборд
# ---------------------------------------------------------------------------

def dashboard(request):
    # Гость (без входа) НЕ редиректится на логин — видит публичный список открытых
    # мероприятий с плашкой «авторизуйтесь» и статусом «Гость» (см. base.html).
    if not request.user.is_authenticated:
        return render(request, 'blog/public_event_list.html', {
            'events': _published_active_events(),
            'is_public': True,
            'show_guest_banner': True,
        })

    user = request.user
    context = {
        'role_label': role_label(user),
        'is_manager': is_manager(user),
        'is_team_leader': is_team_leader(user),
    }
    if is_manager(user):
        context['new_applications_count'] = Application.objects.filter(status='new').count()
        context['open_events_count'] = Event.objects.filter(shifts__recruit_status='open').distinct().count()
    return render(request, 'blog/dashboard.html', context)


# ---------------------------------------------------------------------------
# Мероприятия (Этап 3)
# ---------------------------------------------------------------------------

@login_required
def event_list(request):
    hide_passed = request.GET.get('hide_passed') == '1'
    events = list(
        Event.objects.select_related('location', 'organization').prefetch_related('shifts')
    )

    today = timezone.now().date()
    rows = []
    for event in events:
        days = sorted(s.day for s in event.shifts.all())
        first_day = days[0] if days else None
        last_day = days[-1] if days else None
        is_passed = event.compute_status() in ('passed', 'finished')
        if hide_passed and is_passed:
            continue
        event.first_day = first_day
        event.last_day = last_day
        rows.append(event)

    # Ранжирование по дате: сначала ближайшие предстоящие/идущие (по возрастанию даты),
    # затем прошедшие меро в конце (тоже по возрастанию — недавно прошедшие выше старых).
    # Меро без смен вообще — в самом конце.
    rows.sort(key=lambda e: (
        e.first_day is None,
        e.compute_status() in ('passed', 'finished'),
        e.first_day or datetime.date.max,
    ))

    return render(request, 'blog/event_list.html', {
        'events': rows,
        'hide_passed': hide_passed,
    })


@login_required
def event_detail(request, event_id):
    event = get_object_or_404(Event.objects.select_related('location', 'organization', 'organizer'), pk=event_id)
    event.close_recruit_if_passed()
    shifts = list(event.shifts.select_related('location').order_by('day'))
    # Пока мероприятие не началось — смены идут по возрастанию даты (ближайшая сверху).
    # Прошедшие смены уходят в конец списка, а не сортируются по дате среди актуальных —
    # так самая близкая предстоящая/идущая смена всегда наверху.
    today = timezone.now().date()
    shifts.sort(key=lambda s: (s.day < today, s.day))
    team_leads = event.staff.filter(staff_role='team_lead').select_related('user')
    categories = EventCategory.objects.filter(event_map__event=event).order_by('name')
    can_manage_applications = can_process_applications(request.user, event.id)
    # Мероприятие завершено — заявки/явку менять больше нельзя нигде (ни в вебе, ни
    # через клики по таблице); данные показываются только для просмотра.
    is_finished = event.event_status == 'finished'

    has_open_recruitment = any(s.recruit_status == 'open' for s in shifts)
    my_active_application = Application.objects.filter(
        user=request.user, event=event, status__in=['new', 'approved'],
    ).first()

    context = {
        'event': event,
        'shifts': shifts,
        'team_leads': team_leads,
        'categories': categories,
        'share_url': request.build_absolute_uri(
            reverse('public_event_detail', args=[event.public_token])
        ),
        'can_manage_applications': can_manage_applications,
        'can_edit_applications': can_manage_applications and not is_finished,
        'is_finished': is_finished,
        'is_manager': is_manager(request.user),
        'comments': event.comments.all(),
        'comment_form': EventCommentForm() if can_manage_applications else None,
        'has_open_recruitment': has_open_recruitment,
        'my_active_application': my_active_application,
    }

    # Лента изменений по заявкам (повторный Excel-импорт: кто изменил/отказался/появился) —
    # видна только пока меро не завершено; после завершения скрывается независимо от
    # того, есть ли непрочитанные записи (см. ApplicationChangeLog).
    if can_manage_applications and not is_finished:
        context['application_change_logs'] = event.application_change_logs.all()

    if context['is_manager'] and event.event_status != 'finished':
        context['closing_checklist'] = event_closing_checklist(event)

    if can_manage_applications:
        from blog.services.applications import ensure_shift_decisions

        needed = sum(s.volunteer_needed for s in shifts)
        pending_count = event.applications.filter(status='new').count()

        # Набор разбит по сменам (не общий плоский список — иначе заявка на несколько
        # смен визуально "дублируется" при простом листинге всех applications разом).
        # Статус решения (одобрена/резерв/новая) теперь per-shift через
        # ApplicationShiftDecision — принять/отклонить заявку на одну смену не трогает
        # её статус на другие смены той же заявки.
        for app in event.applications.exclude(status='cancelled').prefetch_related('selected_shifts', 'event__shifts'):
            ensure_shift_decisions(app)

        shifts_with_applications = []
        approved_total = 0
        for shift in shifts:
            decisions = (
                ApplicationShiftDecision.objects.filter(shift=shift)
                .exclude(application__status='cancelled')
                .select_related('application', 'application__user')
                .order_by('-application__created_at')
            )
            shift_approved = decisions.filter(status='approved').count()
            approved_total += shift_approved
            shifts_with_applications.append({
                'shift': shift,
                'decisions': decisions,
                'approved_count': shift_approved,
                'needed': shift.volunteer_needed,
                'remaining': max(shift.volunteer_needed - shift_approved, 0),
            })

        context.update({
            'shifts_with_applications': shifts_with_applications,
            'needed': needed,
            'approved_count': approved_total,
            'remaining': max(needed - approved_total, 0),
            'pending_count': pending_count,
        })

        # Явка по сменам: участники (Participation) уже созданы автоматически при approve
        # заявки на все смены меро (db.md, правило 4) — здесь просто группируем их по смене,
        # чтобы проставить явку чекбоксом «Был» прямо в таблице.
        shifts_with_participations = []
        for shift in shifts:
            participations = shift.participations.select_related('user').order_by('user__last_name', 'user__first_name')
            shifts_with_participations.append({'shift': shift, 'participations': participations})
        context['shifts_with_participations'] = shifts_with_participations

    # 👥 Расширенная карточка — только для участников мероприятия
    # (reports.md раздел 4: подавших заявку или получивших участие).
    is_participant = (
        Application.objects.filter(user=request.user, event=event).exists()
        or Participation.objects.filter(user=request.user, event_day__event=event).exists()
    )
    if is_participant:
        volunteers_count = Participation.objects.filter(event_day__event=event).values('user').distinct().count()
        avg_rating = event.feedbacks.aggregate(avg=Avg('rating'))['avg']
        my_feedback = event.feedbacks.filter(author=request.user).first()
        context.update({
            'is_participant': True,
            'volunteers_count': volunteers_count,
            'avg_rating': avg_rating,
            'feedback_count': event.feedbacks.count(),
            'my_feedback': my_feedback,
            'feedback_form': EventFeedbackForm() if not my_feedback else None,
            # Отзывы волонтёров — видно, кто оставил (в отличие от внутренних
            # комментариев менеджеров/ТЛ, которые показываются без подписи автора).
            'feedbacks': event.feedbacks.select_related('author').order_by('-created_at'),
        })

    return render(request, 'blog/event_detail.html', context)


@login_required
def event_comment_add(request, event_id):
    """Внутренний комментарий к мероприятию (менеджеры/тим-лидеры своего меро) —
    показывается на странице меро без подписи автора, только текст и дата."""
    event = get_object_or_404(Event, pk=event_id)
    if not can_process_applications(request.user, event.id):
        raise PermissionDenied('Нет прав оставлять комментарии к этому мероприятию.')

    if request.method == 'POST':
        form = EventCommentForm(request.POST)
        if form.is_valid():
            comment = form.save(commit=False)
            comment.event = event
            comment.author = request.user
            comment.save()
            messages.success(request, 'Комментарий добавлен.')
    return redirect('event_detail', event_id=event.id)


@login_required
def event_change_log_mark_read(request, event_id):
    """«Прочитано» по ленте изменений заявок мероприятия — полностью очищает её
    (пользователь явно подтвердил, что ознакомился)."""
    event = get_object_or_404(Event, pk=event_id)
    if not can_process_applications(request.user, event.id):
        raise PermissionDenied('Нет прав на это действие.')
    if request.method == 'POST':
        event.application_change_logs.all().delete()
        messages.success(request, 'Изменения по заявкам отмечены как прочитанные.')
    return redirect('event_detail', event_id=event.id)


@login_required
def event_feedback_add(request, event_id):
    event = get_object_or_404(Event, pk=event_id)
    is_participant = (
        Application.objects.filter(user=request.user, event=event).exists()
        or Participation.objects.filter(user=request.user, event_day__event=event).exists()
    )
    if not is_participant:
        raise PermissionDenied('Оставить оценку могут только участники мероприятия.')

    if EventFeedback.objects.filter(event=event, author=request.user).exists():
        messages.error(request, 'Вы уже оставили оценку этому мероприятию.')
        return redirect('event_detail', event_id=event.id)

    if request.method == 'POST':
        form = EventFeedbackForm(request.POST)
        if form.is_valid():
            feedback = form.save(commit=False)
            feedback.event = event
            feedback.author = request.user
            feedback.save()
            messages.success(request, 'Спасибо за оценку!')
    return redirect('event_detail', event_id=event.id)


@manager_required
def event_create(request):
    form = EventForm(request.POST or None, assigned_by=request.user)
    if request.method == 'POST' and form.is_valid():
        event = form.save()
        messages.success(request, 'Мероприятие создано.')
        return modal_form_response(
            request, None, None,
            success_redirect=reverse('event_detail', args=[event.id]),
        )
    return modal_form_response(
        request, 'blog/event_form.html',
        {'form': form, 'title': 'Новое мероприятие', 'submit_label': 'Создать'},
        partial_template='blog/partials/_event_form_body.html',
    )


@manager_required
def event_edit(request, event_id):
    event = get_object_or_404(Event, pk=event_id)
    if event.event_status == 'finished':
        raise PermissionDenied('Завершённое мероприятие нельзя редактировать.')
    form = EventForm(request.POST or None, instance=event, show_closing_fields=True, assigned_by=request.user)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Мероприятие обновлено.')
        return modal_form_response(
            request, None, None,
            success_redirect=reverse('event_detail', args=[event.id]),
        )
    return modal_form_response(
        request, 'blog/event_form.html',
        {'form': form, 'title': f'Редактировать: {event.title}', 'submit_label': 'Сохранить'},
        partial_template='blog/partials/_event_form_body.html',
    )


@manager_required
def event_publish(request, event_id):
    """Публикация мероприятия: проставляет published_at — после этого открывается
    подача заявок и меро попадает в список активных. Рассылку уведомлений НЕ делает —
    это отдельная кнопка «Сделать рассылку» (см. event_broadcast)."""
    event = get_object_or_404(Event, pk=event_id)
    if request.method == 'POST':
        if event.published_at:
            messages.error(request, 'Мероприятие уже опубликовано.')
        elif event.event_status == 'finished':
            messages.error(request, 'Мероприятие уже завершено — публиковать его больше нельзя.')
        else:
            event.published_at = timezone.now()
            event.save(update_fields=['published_at'])
            messages.success(request, 'Мероприятие опубликовано — подача заявок открыта.')
    return redirect('event_detail', event_id=event.id)


@manager_required
def event_broadcast(request, event_id):
    """Рассылка-оповещение о мероприятии подписанным волонтёрам (отдельно от публикации).
    Доступна только после публикации; ставит уведомления в очередь бота."""
    event = get_object_or_404(Event, pk=event_id)
    if request.method == 'POST':
        if not event.published_at:
            messages.error(request, 'Сначала опубликуйте мероприятие — потом можно делать рассылку.')
        elif event.event_status == 'finished':
            messages.error(request, 'Мероприятие завершено — рассылка больше не нужна.')
        elif event.broadcast_sent_at:
            messages.error(request, 'Рассылка по этому мероприятию уже отправлена.')
        else:
            notifications_service.broadcast_new_event(event)
            event.broadcast_sent_at = timezone.now()
            event.save(update_fields=['broadcast_sent_at'])
            messages.success(request, 'Рассылка поставлена в очередь — уведомления уйдут подписанным волонтёрам.')
    return redirect('event_detail', event_id=event.id)


@login_required
def shift_toggle_recruit(request, event_id, shift_id):
    """Ручное переключение набора открыт/закрыт на конкретной смене — доступно
    менеджеру и тим-лидеру мероприятия. Не проверяет необработанные заявки — они
    просто остаются необработанными и видны в общем списке заявок."""
    event = get_object_or_404(Event, pk=event_id)
    shift = get_object_or_404(EventShift, pk=shift_id, event=event)
    if not can_process_applications(request.user, event.id):
        raise PermissionDenied('Нет прав управлять набором этого мероприятия.')
    if event.event_status == 'finished':
        raise PermissionDenied('Мероприятие завершено — набор менять больше нельзя.')

    if request.method == 'POST':
        shift.recruit_status = 'closed' if shift.recruit_status == 'open' else 'open'
        shift.save(update_fields=['recruit_status'])
        label = 'закрыт' if shift.recruit_status == 'closed' else 'открыт'
        messages.success(request, f'Набор на смену {shift.day} {label}.')
    return redirect('event_detail', event_id=event.id)


@manager_required
def event_close(request, event_id):
    """Экран проверки перед завершением мероприятия (cjm-event-lifecycle.md, этап 8):
    показывает чеклист условий, даёт заполнить people_helped, если он ещё пуст,
    и требует явное подтверждение на самой кнопке завершения."""
    event = get_object_or_404(Event, pk=event_id)
    checklist = event_closing_checklist(event)

    if request.method == 'POST':
        if not checklist['ready']:
            messages.error(request, 'Мероприятие ещё не готово к завершению — проверьте условия ниже.')
            return redirect('event_close', event_id=event.id)

        people_helped = request.POST.get('people_helped', '').strip()
        if not people_helped.isdigit():
            messages.error(request, 'Укажите количество людей, которым помогли (обязательное поле).')
            return redirect('event_close', event_id=event.id)

        event.people_helped = int(people_helped)
        event.event_status = 'finished'
        event.save(update_fields=['people_helped', 'event_status'])
        messages.success(request, f'Мероприятие «{event.title}» завершено.')
        return redirect('event_detail', event_id=event.id)

    return render(request, 'blog/event_close.html', {'event': event, 'checklist': checklist})


@manager_required
def event_shift_add(request, event_id):
    event = get_object_or_404(Event, pk=event_id)
    if event.event_status == 'finished':
        raise PermissionDenied('Завершённое мероприятие нельзя редактировать.')
    form = EventShiftForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        shift = form.save(commit=False)
        shift.event = event
        shift.save()
        messages.success(request, 'Смена мероприятия добавлена.')
        return modal_form_response(
            request, None, None,
            success_redirect=reverse('event_detail', args=[event.id]),
        )
    return modal_form_response(
        request, 'blog/event_shift_form.html',
        {'form': form, 'event': event, 'title': f'Новая смена: {event.title}', 'submit_label': 'Добавить'},
        partial_template='blog/partials/_event_shift_form_body.html',
    )


@manager_required
def event_shift_edit(request, event_id, shift_id):
    event = get_object_or_404(Event, pk=event_id)
    if event.event_status == 'finished':
        raise PermissionDenied('Завершённое мероприятие нельзя редактировать.')
    shift = get_object_or_404(EventShift, pk=shift_id, event=event)
    form = EventShiftForm(request.POST or None, instance=shift)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Смена мероприятия обновлена.')
        return modal_form_response(
            request, None, None,
            success_redirect=reverse('event_detail', args=[event.id]),
        )
    return modal_form_response(
        request, 'blog/event_shift_form.html',
        {'form': form, 'event': event, 'title': f'Редактировать смену: {event.title}', 'submit_label': 'Сохранить'},
        partial_template='blog/partials/_event_shift_form_body.html',
    )


@manager_required
def event_shift_delete(request, event_id, shift_id):
    event = get_object_or_404(Event, pk=event_id)
    if event.event_status == 'finished':
        raise PermissionDenied('Завершённое мероприятие нельзя редактировать.')
    shift = get_object_or_404(EventShift, pk=shift_id, event=event)
    if request.method == 'POST':
        shift.delete()
        messages.success(request, 'Смена мероприятия удалена.')
    return redirect('event_detail', event_id=event.id)



# ---------------------------------------------------------------------------
# Явка (Этап 5)
# ---------------------------------------------------------------------------

@login_required
def participation_set_status(request, participation_id):
    """Простановка явки прямо в таблице на странице мероприятия (AJAX), без отдельной
    формы/подтверждения: явка (был/не был) + при "не был" — причина (уважительно/без
    предупреждения). Часы и full_shift считаются автоматически, а не вводятся вручную:
    - «Был» → hours = длительность дня (end_time - start_time), full_shift=True.
    - «Отсутствовал» (обе причины) → hours=0, full_shift=False.
    Единственное явное последствие для волонтёра — churn при неявке без предупреждения."""
    if request.method != 'POST':
        return JsonResponse({'error': 'Метод не поддерживается.'}, status=405)

    participation = get_object_or_404(
        Participation.objects.select_related('event_day__event', 'user'), pk=participation_id
    )
    event = participation.event_day.event
    if not can_process_applications(request.user, event.id):
        raise PermissionDenied('Нет доступа к явке этого мероприятия.')
    if event.event_status == 'finished':
        return JsonResponse({'error': 'Мероприятие завершено — явку менять больше нельзя.'}, status=403)

    new_status = request.POST.get('status')
    if new_status not in dict(Participation.STATUS_CHOICES):
        return JsonResponse({'error': 'Некорректный статус.'}, status=400)

    old_status = participation.status
    participation.status = new_status
    if new_status == 'attended':
        day = participation.event_day
        duration = day.end_time - day.start_time
        participation.hours = max(0, round(duration.total_seconds() / 3600))
        participation.full_shift = True
    else:
        participation.hours = 0
        participation.full_shift = False
    participation.save(update_fields=['status', 'hours', 'full_shift'])
    recalc_user_aggregates(participation.user)

    if new_status == 'absent_unexcused' and old_status != 'absent_unexcused':
        from blog.services.stats import apply_churn_no_show
        apply_churn_no_show(participation.user)
    elif new_status == 'attended' and old_status != 'attended':
        from blog.services.stats import apply_churn_activity_decay
        apply_churn_activity_decay(participation.user)

    return JsonResponse({
        'status': participation.status,
        'status_display': participation.get_status_display(),
        'hours': participation.hours,
    })


@login_required
def participation_edit(request, participation_id):
    participation = get_object_or_404(
        Participation.objects.select_related('event_day__event', 'user'), pk=participation_id
    )
    event = participation.event_day.event
    if not can_process_applications(request.user, event.id):
        raise PermissionDenied('Нет доступа к явке этого мероприятия.')
    if event.event_status == 'finished':
        raise PermissionDenied('Мероприятие завершено — явку менять больше нельзя.')

    form = ParticipationForm(request.POST or None, instance=participation)
    if request.method == 'POST' and form.is_valid():
        old_status = Participation.objects.get(pk=participation.pk).status
        participation = form.save()
        recalc_user_aggregates(participation.user)

        if participation.status == 'absent_unexcused' and old_status != 'absent_unexcused':
            from blog.services.stats import apply_churn_no_show
            apply_churn_no_show(participation.user)
        elif participation.status == 'attended':
            from blog.services.stats import apply_churn_activity_decay
            apply_churn_activity_decay(participation.user)

        messages.success(request, 'Явка обновлена, статистика пересчитана.')
        return modal_form_response(
            request, None, None,
            success_redirect=reverse('event_detail', args=[event.id]),
        )

    return modal_form_response(
        request, 'blog/participation_form.html',
        {'form': form, 'participation': participation, 'title': f'Явка: {participation.user.full_name}'},
    )


# ---------------------------------------------------------------------------
# Заявки (Этап 4)
# ---------------------------------------------------------------------------

def _fill_application_shifts(application, selected_shifts):
    if selected_shifts:
        application.selected_shifts.set(selected_shifts)
        application.shifts = ', '.join(s.day.isoformat() for s in selected_shifts.order_by('day'))
    else:
        # Смены не выбраны — считаем заявку на все открытые смены меро (одна смена
        # или пользователь ничего не отмечал вручную).
        all_shifts = application.event.shifts.filter(recruit_status='open').order_by('day')
        application.selected_shifts.set(all_shifts)
        application.shifts = 'Все смены'
    application.save(update_fields=['shifts'])


@login_required
def application_create(request):
    """Заявка через веб: ФИО/Telegram/телефон берём из профиля авторизованного
    пользователя — система их уже знает, вводить заново не нужно.

    Дубли на веб-форме не допускаются (в отличие от Excel-импорта, db.md правило 3,
    которое остаётся как есть): если у пользователя уже есть активная заявка
    (new/approved) на выбранное меро, вместо создания второй — редирект на
    редактирование существующей."""
    # Подать заявку может только пользователь с «заполненным аккаунтом» (известны ФИО
    # и Telegram). Если ФИО ещё нет — сначала дозаполнение профиля, потом возврат сюда.
    if not account_is_complete(request.user):
        messages.info(request, 'Заполните профиль (ФИО), чтобы подать заявку.')
        return redirect(reverse('complete_profile') + f'?next={quote(request.get_full_path())}')

    event_id = request.POST.get('event') or request.GET.get('event')
    if event_id:
        # Подать заявку можно только на опубликованное мероприятие (кнопка «Опубликовать»
        # открывает набор). До публикации меро не видно волонтёрам и заявки не принимаются.
        target_event = Event.objects.filter(pk=event_id).first()
        if target_event and not target_event.published_at:
            messages.error(request, 'Мероприятие ещё не опубликовано — подать заявку нельзя.')
            return redirect('dashboard')
        existing = Application.objects.filter(
            user=request.user, event_id=event_id, status__in=['new', 'approved'],
        ).first()
        if existing:
            messages.info(request, 'Заявка на это мероприятие уже подана — вы можете отредактировать её.')
            return redirect('application_edit', application_id=existing.id)

    initial = {'event': event_id} if event_id and request.method != 'POST' else None
    form = ApplicationForm(request.POST or None, initial=initial, user=request.user)
    if request.method == 'POST' and form.is_valid():
        application = form.save(commit=False)
        application.submitted_at = timezone.now()
        application.user = request.user
        application.first_name = request.user.first_name or ''
        application.last_name = request.user.last_name
        application.telegram_username = request.user.telegram_username
        application.phone = request.user.phone
        application.save()
        _fill_application_shifts(application, form.cleaned_data.get('selected_shifts'))
        notifications_service.notify_application_submitted(application)
        messages.success(request, 'Заявка отправлена.')
        return modal_form_response(request, None, None, success_redirect=reverse('dashboard'))
    return modal_form_response(
        request, 'blog/application_form.html',
        {'form': form, 'title': 'Подать заявку на мероприятие', 'submit_label': 'Отправить заявку'},
        partial_template='blog/partials/_application_form_body.html',
    )


@login_required
def application_edit(request, application_id):
    """Редактирование своей заявки (смены/функция/роль) — вместо создания дубля."""
    application = get_object_or_404(Application, pk=application_id, user=request.user)
    if application.status not in ('new', 'approved'):
        messages.error(request, 'Эта заявка уже неактивна и не может быть отредактирована.')
        return redirect('dashboard')

    form = ApplicationForm(request.POST or None, user=request.user, instance=application)
    if request.method == 'POST' and form.is_valid():
        application = form.save(commit=False)
        application.save()
        _fill_application_shifts(application, form.cleaned_data.get('selected_shifts'))
        messages.success(request, 'Заявка обновлена.')
        return modal_form_response(request, None, None, success_redirect=reverse('dashboard'))
    return modal_form_response(
        request, 'blog/application_form.html',
        {'form': form, 'title': 'Редактировать заявку', 'submit_label': 'Сохранить', 'application': application},
        partial_template='blog/partials/_application_form_body.html',
    )


@login_required
def application_cancel(request, application_id):
    """Отмена заявки волонтёром — только пока до ближайшей выбранной смены осталось
    больше `const.late_cancel_hours`; причина обязательна. Правило — общее с ботом,
    см. `blog.services.applications.can_cancel_application`."""
    application = get_object_or_404(Application, pk=application_id, user=request.user)
    can_cancel, reason_code, late_cancel_hours = can_cancel_application(application)

    if not can_cancel:
        if reason_code == 'inactive':
            messages.error(request, 'Эта заявка уже неактивна.')
        else:
            messages.error(
                request,
                f'Отменить заявку можно не позднее чем за {late_cancel_hours:g} ч. до начала мероприятия — '
                'обратитесь к тим-лидеру или менеджеру напрямую.',
            )
        return redirect('dashboard')

    form = ApplicationCancelForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        cancel_application(application, form.cleaned_data['reason'])
        messages.success(request, 'Заявка отменена.')
        return modal_form_response(request, None, None, success_redirect=reverse('dashboard'))
    return modal_form_response(
        request, 'blog/application_cancel_form.html',
        {'form': form, 'title': f'Отменить заявку на «{application.event.title}»', 'submit_label': 'Отменить заявку', 'application': application},
    )


@login_required
def event_shifts_json(request, event_id):
    """Список открытых смен мероприятия — для чип-виджета выбора смен в форме заявки
    (JS дозапрашивает при смене выбранного мероприятия, аналог поиска справочников)."""
    event = get_object_or_404(Event, pk=event_id)
    shifts = event.shifts.filter(recruit_status='open').order_by('day')
    return JsonResponse({
        'shifts': [
            {'id': s.id, 'label': f'{s.day.strftime("%d.%m.%Y")} {s.start_time.strftime("%H:%M")}–{s.end_time.strftime("%H:%M")}'}
            for s in shifts
        ],
    })


@login_required
def application_process(request, application_id):
    application = get_object_or_404(Application.objects.select_related('event'), pk=application_id)
    if not can_process_applications(request.user, application.event_id):
        raise PermissionDenied('Нет прав на обработку заявок этого мероприятия.')
    if application.event.event_status == 'finished':
        raise PermissionDenied('Мероприятие завершено — заявки менять больше нельзя.')

    # Возвращаемся туда, откуда пришёл запрос (страница мероприятия или
    # список необработанных заявок), а не всегда на карточку мероприятия.
    next_url = request.POST.get('next')
    if next_url:
        redirect_url = next_url
    else:
        redirect_url = reverse('event_detail', args=[application.event_id]) + '#applications'

    new_status = request.POST.get('status')
    if new_status not in ('approved', 'rejected'):
        messages.error(request, 'Некорректный статус.')
        return redirect(redirect_url)

    process_application(application, new_status, request.user)
    messages.success(request, f'Заявка #{application.id} обработана: {application.get_status_display()}.')
    return redirect(redirect_url)


@login_required
def application_shift_process(request, application_id, shift_id):
    """Принять/отклонить заявку ТОЛЬКО на одну смену (таблица набора на event_detail,
    где заявка на несколько смен показывается в таблице каждой смены отдельно) —
    остальные смены той же заявки не затрагиваются (ApplicationShiftDecision)."""
    application = get_object_or_404(Application.objects.select_related('event'), pk=application_id)
    shift = get_object_or_404(EventShift, pk=shift_id, event=application.event)
    if not can_process_applications(request.user, application.event_id):
        raise PermissionDenied('Нет прав на обработку заявок этого мероприятия.')
    if application.event.event_status == 'finished':
        raise PermissionDenied('Мероприятие завершено — заявки менять больше нельзя.')

    redirect_url = reverse('event_detail', args=[application.event_id]) + '#applications'
    new_status = request.POST.get('status')
    if new_status not in ('approved', 'rejected'):
        messages.error(request, 'Некорректный статус.')
        return redirect(redirect_url)

    from blog.services.applications import process_shift_decision
    process_shift_decision(application, shift, new_status, request.user)
    messages.success(request, f'Заявка на смену {shift.day.strftime("%d.%m.%Y")} обработана.')
    return redirect(redirect_url)


def _pending_applications_queryset(event_id=None):
    qs = Application.objects.filter(status='new').select_related('event')
    if event_id:
        qs = qs.filter(event_id=event_id)
    return qs


@login_required
def application_process_all(request, event_id=None):
    """«Принять всех» / «Обработать все заявки» — approve всех new-заявок сразу
    (в рамках одного мероприятия, если event_id передан, иначе глобально).

    Если одобрение всех заявок даёт перебор относительно нужного количества
    волонтёров — сначала показываем страницу-уточнение с расчётом (на сколько
    человек больше, % от заявленного) и явным подтверждением; без перебора —
    принимаем сразу."""
    event = get_object_or_404(Event, pk=event_id) if event_id else None
    if event:
        if not can_process_applications(request.user, event.id):
            raise PermissionDenied('Нет прав на обработку заявок этого мероприятия.')
        if event.event_status == 'finished':
            raise PermissionDenied('Мероприятие завершено — заявки менять больше нельзя.')
    elif not is_manager(request.user):
        raise PermissionDenied('Нет прав на обработку заявок.')

    pending = _pending_applications_queryset(event_id)
    pending_count = pending.count()

    redirect_url = reverse('event_detail', args=[event.id]) + '#applications' if event else reverse('pending_applications')

    if pending_count == 0:
        messages.info(request, 'Необработанных заявок нет.')
        return redirect(redirect_url)

    needed = sum(s.volunteer_needed for s in event.shifts.all()) if event else None
    approved_now = event.applications.filter(status='approved').count() if event else None
    overflow = None
    if needed:
        total_after = approved_now + pending_count
        if total_after > needed:
            overflow = {
                'extra': total_after - needed,
                'pct': round(total_after / needed * 100),
                'needed': needed,
                'total_after': total_after,
            }

    confirmed = request.POST.get('confirm') == 'yes'
    if overflow and not confirmed:
        # Первый заход (без явного "да, принять всех") — показываем расчёт перебора,
        # ничего не меняем. Работает и для POST от кнопки «Принять всех», и для GET.
        return render(request, 'blog/application_process_all_confirm.html', {
            'event': event,
            'pending_count': pending_count,
            'overflow': overflow,
            'redirect_url': redirect_url,
            'confirm_action_url': request.path,
        })

    for application in pending:
        process_application(application, 'approved', request.user)
    messages.success(request, f'Принято заявок: {pending_count}.')
    return redirect(redirect_url)


@manager_required
def import_preview(request):
    form = ExcelImportForm(request.POST or None, request.FILES or None)
    if request.method == 'POST' and form.is_valid():
        result = import_service.parse_excel(request.FILES['excel_file'])
        request.session['import_rows'] = [
            {**row, 'submitted_at': row['submitted_at'].isoformat() if row.get('submitted_at') else None}
            for row in result['rows']
        ]
        return render(request, 'blog/import_preview.html', {
            'rows': result['rows'], 'errors': result['errors'],
        })
    return render(request, 'blog/import_form.html', {'form': form})


@manager_required
def import_commit(request):
    if request.method != 'POST':
        return redirect('import_preview')

    rows = request.session.pop('import_rows', [])
    for row in rows:
        if row.get('submitted_at'):
            row['submitted_at'] = datetime.datetime.fromisoformat(row['submitted_at'])

    result = import_service.commit_import(rows)
    msg = f'Новых заявок: {result["created"]}.'
    if result['updated']:
        msg += f' Обновлено (изменилось содержание): {result["updated"]}.'
    if result['skipped']:
        msg += f' Без изменений: {result["skipped"]}.'
    messages.success(request, msg)
    return redirect('event_list')


# ---------------------------------------------------------------------------
# Статистика / профиль (Этап 5-6, публичный + расширенный уровень)
# ---------------------------------------------------------------------------

@login_required
def profile_detail(request, user_id):
    target = get_object_or_404(User, pk=user_id)
    is_owner = request.user.id == target.id
    is_mgr = is_manager(request.user)
    # Расширенный + внутренний профиль: владелец, менеджер, либо ТЛ в контексте
    # своего мероприятия (access-control.md, правило 5).
    has_extended = has_extended_access_to_profile(request.user, target)
    # Комментарии волонтёр не видит никогда, даже о себе (access-control.md, правило 6).
    can_see_comments = is_mgr or (is_team_leader(request.user) and has_extended and not is_owner)

    from blog.services.experience import build_experience
    experience = build_experience(target)

    context = {
        'target': target,
        'is_owner': is_owner,
        'is_manager': is_mgr,
        'has_extended_access': has_extended,
        'can_see_comments': can_see_comments,
        'past_participations': target.participations.filter(status='attended').select_related('event_day__event').order_by('-event_day__day')[:50],
        'experience': experience,
        # Ссылка «Скопировать опыт» — публичная страница опыта по токену (раздел 4).
        'experience_share_url': request.build_absolute_uri(
            reverse('public_experience', args=[target.experience_token])
        ),
        'experience_form': ExternalExperienceForm() if is_owner else None,
    }

    # Публичный блок ТЛ/менеджера (reports.md: кол-во меро как ТЛ, дата назначения).
    tl_role = target.team_leader_roles.filter(revoked_at__isnull=True).first()
    if tl_role:
        context['tl_role'] = tl_role
        context['tl_event_count'] = EventStaff.objects.filter(
            user=target, staff_role='team_lead'
        ).count()
    mgr_role = target.manager_roles.filter(revoked_at__isnull=True).first()
    if mgr_role:
        context['mgr_role'] = mgr_role

    if has_extended:
        context['applications'] = target.applications.select_related('event').order_by('-created_at')
        # Пул благодарственных писем: мероприятия с явкой и заданным шаблоном.
        context['letters_pool'] = (
            Event.objects.filter(
                shifts__participations__user=target,
                shifts__participations__status='attended',
                letter__isnull=False,
            )
            .distinct()
            .select_related('letter')
            .order_by('-created_at')
        )

    if is_mgr:
        context['churn_history'] = target.churn_history.select_related('season').order_by('-created_at')

    if can_see_comments:
        context['comments'] = target.received_comments.select_related('author', 'event').order_by('-created_at')
        if is_mgr:
            event_queryset = Event.objects.all()
        else:
            event_queryset = Event.objects.filter(
                staff__user=request.user, staff__staff_role='team_lead'
            ).exclude(event_status='finished')
        context['comment_form'] = UserCommentForm(event_queryset=event_queryset)

    return render(request, 'blog/profile_detail.html', context)


@login_required
def letter_view(request, user_id, event_id):
    """Благодарственное письмо — генерируется на лету, PDF не хранится
    (implementation-plan Этап 6.4). Страница свёрстана под печать/сохранение в PDF.

    Доступ: сам волонтёр, менеджер или ТЛ с расширенным доступом к профилю.
    """
    target = get_object_or_404(User, pk=user_id)
    event = get_object_or_404(Event.objects.select_related('letter', 'organization'), pk=event_id)

    if not has_extended_access_to_profile(request.user, target):
        raise PermissionDenied('Нет доступа к письмам этого волонтёра.')
    if not event.letter:
        raise PermissionDenied('У мероприятия не задан шаблон благодарственного письма.')

    attended = Participation.objects.filter(
        user=target, event_day__event=event, status='attended'
    ).select_related('event_day')
    if not attended.exists():
        raise PermissionDenied('Волонтёр не участвовал в этом мероприятии.')

    hours = sum(p.hours for p in attended)
    last_day = max(p.event_day.day for p in attended)

    return render(request, 'blog/letter.html', {
        'target': target,
        'event': event,
        'letter': event.letter,
        'hours': hours,
        'last_day': last_day,
    })


@manager_required
def pending_applications(request):
    applications = Application.objects.filter(status='new').select_related('event', 'user').order_by('created_at')
    return render(request, 'blog/pending_applications.html', {
        'applications': applications,
        'pending_count': applications.count(),
    })


@login_required
def profile_comment_add(request, user_id):
    target = get_object_or_404(User, pk=user_id)
    is_mgr = is_manager(request.user)
    can_comment = is_mgr or (
        is_team_leader(request.user)
        and has_extended_access_to_profile(request.user, target)
        and request.user.id != target.id
    )
    if not can_comment:
        raise PermissionDenied('Нет прав оставлять комментарии этому волонтёру.')

    if is_mgr:
        event_queryset = Event.objects.all()
    else:
        event_queryset = Event.objects.filter(
            staff__user=request.user, staff__staff_role='team_lead'
        ).exclude(event_status='finished')

    if request.method == 'POST':
        form = UserCommentForm(request.POST, event_queryset=event_queryset)
        if form.is_valid():
            comment = form.save(commit=False)
            comment.target_user = target
            comment.author = request.user
            comment.save()
            messages.success(request, 'Комментарий добавлен.')
    return redirect('profile_detail', user_id=target.id)


# ---------------------------------------------------------------------------
# Отчёты (Этап 5, reports.md раздел 7)
# ---------------------------------------------------------------------------

def _report_period_range(request):
    """Диапазон дат для отчёта: пресет (`period`) или произвольный диапазон
    (`date_from`/`date_to` — brandbook §6.3, date range picker, стиль Booking.com)."""
    today = timezone.now().date()

    raw_from = request.GET.get('date_from')
    raw_to = request.GET.get('date_to')
    if raw_from and raw_to:
        try:
            date_from = datetime.date.fromisoformat(raw_from)
            date_to = datetime.date.fromisoformat(raw_to)
            if date_from <= date_to:
                return 'custom', date_from, date_to
        except ValueError:
            pass

    period = request.GET.get('period', 'month')
    if period == 'yesterday':
        date_from = date_to = today - datetime.timedelta(days=1)
        return period, date_from, date_to
    if period == 'today':
        return period, today, today
    if period == 'week':
        return period, today - datetime.timedelta(days=7), today
    if period == 'season':
        current = Season.objects.filter(start_season__lte=today).order_by('-start_season').first()
        if current:
            return period, current.start_season, current.end_season or today
        return period, today - datetime.timedelta(days=120), today
    if period == 'last_season':
        current = Season.objects.filter(start_season__lte=today).order_by('-start_season').first()
        previous_qs = Season.objects.order_by('-start_season')
        if current:
            previous_qs = previous_qs.filter(start_season__lt=current.start_season)
        previous = previous_qs.first()
        if previous:
            return period, previous.start_season, previous.end_season or (current.start_season - datetime.timedelta(days=1) if current else today)
        return period, today - datetime.timedelta(days=240), today - datetime.timedelta(days=120)

    period = 'month'
    return period, today - datetime.timedelta(days=30), today


def _report_scope(request):
    """Охват отчёта — два взаимоисключающих режима (reports_template.md «Выбор охвата»):
    `scope=events` + `event_ids=1,2,3` — по явному списку мероприятий (даты игнорируются),
    иначе — по периоду через `_report_period_range`. Возвращает
    (scope_mode, period, date_from, date_to, event_ids)."""
    raw_event_ids = request.GET.get('event_ids', '').strip()
    if request.GET.get('scope') == 'events' and raw_event_ids:
        event_ids = [int(x) for x in raw_event_ids.split(',') if x.strip().isdigit()]
        if event_ids:
            return 'events', None, None, None, event_ids

    period, date_from, date_to = _report_period_range(request)
    return 'period', period, date_from, date_to, None


@manager_required
def report_period(request):
    scope_mode, period, date_from, date_to, event_ids = _report_scope(request)
    compare_mode = request.GET.get('compare', 'adjacent')
    if compare_mode not in ('adjacent', 'year_ago'):
        compare_mode = 'adjacent'

    prev_from = prev_to = None
    if scope_mode == 'period':
        prev_from, prev_to = reports_service.previous_period_range(date_from, date_to, compare_mode)

    data = reports_service.period_report(date_from, date_to, prev_from, prev_to, event_ids=event_ids)
    efficiency = reports_service.recruitment_efficiency(date_from, date_to, event_ids=event_ids)
    staff = reports_service.staff_load(date_from, date_to, event_ids=event_ids)
    weekly = reports_service.weekly_dynamics(date_from, date_to, event_ids=event_ids)
    timing = reports_service.application_timing(date_from, date_to, event_ids=event_ids)
    post_timing = reports_service.post_publication_timing(date_from, date_to, event_ids=event_ids)

    def _event_brief(event):
        first_shift_day = event.shifts.order_by('day').values_list('day', flat=True).first()
        return {'id': event.id, 'title': event.title, 'date': first_shift_day.strftime('%d.%m.%Y') if first_shift_day else None}

    selected_events = []
    if event_ids:
        selected_events = list(Event.objects.filter(id__in=event_ids).order_by('-created_at'))

    recent_events = [_event_brief(e) for e in Event.objects.order_by('-created_at')[:15]]

    context = {
        'scope_mode': scope_mode,
        'period': period,
        'date_from': date_from,
        'date_to': date_to,
        'event_ids': event_ids or [],
        'selected_events': selected_events,
        'compare_mode': compare_mode,
        'report': data,
        'efficiency': efficiency,
        'staff': staff,
        'weekly': weekly,
        'timing': timing,
        'post_timing': post_timing,
        'recent_events_json': recent_events,
        'selected_events_json': [_event_brief(e) for e in selected_events],
    }
    return render(request, 'blog/report_period.html', context)


@manager_required
def report_events_search(request):
    """Поиск/список мероприятий для модалки выбора охвата «по мероприятиям»
    (reports_template.md: последние 15 сразу, остальное — по запросу с поиском)."""
    query = request.GET.get('q', '').strip()
    events = Event.objects.order_by('-created_at')
    if query:
        events = events.filter(title__icontains=query)
    events = events[:50]

    def _brief(event):
        day = event.shifts.order_by('day').values_list('day', flat=True).first()
        return {'id': event.id, 'title': event.title, 'date': day.strftime('%d.%m.%Y') if day else None}

    return JsonResponse({'events': [_brief(e) for e in events]})


def _xlsx_response(workbook, filename):
    from django.http import HttpResponse
    response = HttpResponse(
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    workbook.save(response)
    return response


@manager_required
def report_period_export(request):
    """Excel-отчёт за охват (.xlsx) — reports_template.md §2: агрегатные листы
    (дублируют внутренний PDF) + сырые построчные листы «Заявки» и «Мероприятия»."""
    scope_mode, period, date_from, date_to, event_ids = _report_scope(request)
    compare_mode = request.GET.get('compare', 'adjacent')
    if compare_mode not in ('adjacent', 'year_ago'):
        compare_mode = 'adjacent'

    wb = report_exports.build_period_workbook(date_from, date_to, event_ids, scope_mode, compare_mode)
    filename = f"report_{date_from}_{date_to}.xlsx" if scope_mode == 'period' else "report_events.xlsx"
    return _xlsx_response(wb, filename)


@manager_required
def report_period_pdf(request, variant):
    """Печатный HTML-отчёт за охват (reports_template.md §1). variant='external' — §1A
    (обезличенный, для руководства), 'internal' — §1B (с именами/операционкой).
    Открывается как страница со стилями @media print и кнопкой «Скачать PDF»
    (браузерная печать → Save as PDF; серверная PDF-библиотека не требуется)."""
    if variant not in ('external', 'internal'):
        raise PermissionDenied('Неизвестная вариация отчёта.')

    scope_mode, period, date_from, date_to, event_ids = _report_scope(request)
    compare_mode = request.GET.get('compare', 'adjacent')
    if compare_mode not in ('adjacent', 'year_ago'):
        compare_mode = 'adjacent'

    prev_from = prev_to = None
    if scope_mode == 'period':
        prev_from, prev_to = reports_service.previous_period_range(date_from, date_to, compare_mode)

    data = reports_service.period_report(date_from, date_to, prev_from, prev_to, event_ids=event_ids)
    efficiency = reports_service.recruitment_efficiency(date_from, date_to, event_ids=event_ids)
    timing = reports_service.application_timing(date_from, date_to, event_ids=event_ids)
    post_timing = reports_service.post_publication_timing(date_from, date_to, event_ids=event_ids)

    context = {
        'variant': variant,
        'is_internal': variant == 'internal',
        'scope_mode': scope_mode,
        'date_from': date_from,
        'date_to': date_to,
        'compare_mode': compare_mode,
        'generated_at': timezone.now(),
        'report': data,
        'efficiency': efficiency,
        'timing': timing,
        'post_timing': post_timing,
    }
    if variant == 'internal':
        context['staff'] = reports_service.staff_load(date_from, date_to, event_ids=event_ids)
    return render(request, 'blog/report_pdf_period.html', context)


@manager_required
def report_event_export(request, event_id):
    """Excel-отчёт по одному мероприятию (reports_template.md §4)."""
    event = get_object_or_404(Event, pk=event_id)
    wb = report_exports.build_event_workbook(event.id)
    return _xlsx_response(wb, f"event_{event.id}_report.xlsx")


@manager_required
def report_event_pdf(request, event_id):
    """Печатный HTML-отчёт по одному мероприятию (reports_template.md §4)."""
    event = get_object_or_404(Event, pk=event_id)
    rep = report_exports.event_report(event.id)
    rep['generated_at'] = timezone.now()
    return render(request, 'blog/report_pdf_event.html', rep)


@manager_required
def report_dobro_export(request):
    """Excel-отчёт простановки часов на Добро.ру (reports_template.md §5):
    листы «По ссылке», «По человеку», «Сводка»."""
    scope_mode, period, date_from, date_to, event_ids = _report_scope(request)
    show_logged = request.GET.get('show_logged') == '1'
    wb = report_exports.build_dobro_workbook(date_from, date_to, event_ids, show_logged)
    return _xlsx_response(wb, "dobro_ru_logging.xlsx")


@manager_required
def report_bundle(request):
    """Комплексный отчёт (reports_template.md §3): ZIP из внешнего PDF, внутреннего PDF
    (как самостоятельные .html для печати) и Excel-книги. PDF отдаём печатным HTML,
    т.к. серверной PDF-библиотеки в проекте нет (по договорённости)."""
    import io
    import zipfile
    from django.http import HttpResponse
    from django.template.loader import render_to_string

    scope_mode, period, date_from, date_to, event_ids = _report_scope(request)
    compare_mode = request.GET.get('compare', 'adjacent')
    if compare_mode not in ('adjacent', 'year_ago'):
        compare_mode = 'adjacent'

    prev_from = prev_to = None
    if scope_mode == 'period':
        prev_from, prev_to = reports_service.previous_period_range(date_from, date_to, compare_mode)
    data = reports_service.period_report(date_from, date_to, prev_from, prev_to, event_ids=event_ids)
    efficiency = reports_service.recruitment_efficiency(date_from, date_to, event_ids=event_ids)
    timing = reports_service.application_timing(date_from, date_to, event_ids=event_ids)
    post_timing = reports_service.post_publication_timing(date_from, date_to, event_ids=event_ids)

    base_ctx = {
        'scope_mode': scope_mode, 'date_from': date_from, 'date_to': date_to,
        'compare_mode': compare_mode, 'generated_at': timezone.now(),
        'report': data, 'efficiency': efficiency, 'timing': timing, 'post_timing': post_timing,
        'standalone': True,
    }
    external_html = render_to_string('blog/report_pdf_period.html', {
        **base_ctx, 'variant': 'external', 'is_internal': False,
    }, request=request)
    internal_html = render_to_string('blog/report_pdf_period.html', {
        **base_ctx, 'variant': 'internal', 'is_internal': True,
        'staff': reports_service.staff_load(date_from, date_to, event_ids=event_ids),
    }, request=request)

    wb = report_exports.build_period_workbook(date_from, date_to, event_ids, scope_mode, compare_mode)
    xlsx_buf = io.BytesIO()
    wb.save(xlsx_buf)

    scope_slug = f'{date_from}_{date_to}' if scope_mode == 'period' else 'events'

    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f'report_external_{scope_slug}.html', external_html)
        zf.writestr(f'report_internal_{scope_slug}.html', internal_html)
        zf.writestr(f'report_{scope_slug}.xlsx', xlsx_buf.getvalue())
    zip_buf.seek(0)

    response = HttpResponse(zip_buf.getvalue(), content_type='application/zip')
    response['Content-Disposition'] = f'attachment; filename="report_bundle_{scope_slug}.zip"'
    return response


@manager_required
def location_list(request):
    locations = Location.objects.order_by('name')
    return render(request, 'blog/location_list.html', {'locations': locations})


@manager_required
def location_create(request):
    form = LocationForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Локация создана.')
        return modal_form_response(
            request, None, None,
            success_redirect=reverse('location_list'),
        )
    return modal_form_response(
        request, 'blog/location_form.html',
        {'form': form, 'title': 'Новая локация'},
    )


@manager_required
def location_edit(request, location_id):
    location = get_object_or_404(Location, pk=location_id)
    form = LocationForm(request.POST or None, instance=location)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Локация обновлена.')
        return modal_form_response(
            request, None, None,
            success_redirect=reverse('location_list'),
        )
    return modal_form_response(
        request, 'blog/location_form.html',
        {'form': form, 'title': f'Редактировать: {location.name}', 'submit_label': 'Сохранить'},
    )


@manager_required
def organizations_list(request):
    organizations = Organization.objects.order_by('name')
    return render(request, 'blog/organizations_list.html', {'organizations': organizations})


@manager_required
def organization_create(request):
    form = OrganizationForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        organization = form.save()
        messages.success(request, 'Организация создана.')
        return modal_form_response(
            request, None, None,
            success_redirect=reverse('organization_detail', args=[organization.id]),
        )
    return modal_form_response(
        request, 'blog/organization_form.html',
        {'form': form, 'title': 'Новая организация'},
    )


@manager_required
def organization_edit(request, org_id):
    organization = get_object_or_404(Organization, pk=org_id)
    form = OrganizationForm(request.POST or None, instance=organization)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Организация обновлена.')
        return modal_form_response(
            request, None, None,
            success_redirect=reverse('organization_detail', args=[organization.id]),
        )
    return modal_form_response(
        request, 'blog/organization_form.html',
        {'form': form, 'title': f'Редактировать: {organization.name}', 'submit_label': 'Сохранить'},
    )


@manager_required
def organization_detail(request, org_id):
    organization = get_object_or_404(Organization, pk=org_id)
    events = organization.events.prefetch_related('shifts').order_by('-created_at')
    organizers = Organizer.objects.filter(organization_links__organization=organization).distinct()
    return render(request, 'blog/organization_detail.html', {
        'organization': organization, 'events': events, 'organizers': organizers,
    })


# ---------------------------------------------------------------------------
# Пользователи, организаторы, настройки
# ---------------------------------------------------------------------------

@manager_required
def user_list(request):
    users = User.objects.order_by('-created_at')
    paginator = Paginator(users, 40)
    page = paginator.get_page(request.GET.get('page'))

    manager_ids = set(Manager.objects.filter(revoked_at__isnull=True).values_list('user_id', flat=True))
    team_leader_ids = set(TeamLeader.objects.filter(revoked_at__isnull=True).values_list('user_id', flat=True))

    context = {
        'page_obj': page,
        'manager_ids': manager_ids,
        'team_leader_ids': team_leader_ids,
    }
    if is_ajax(request):
        return render(request, 'blog/partials/_user_list_rows.html', context)
    return render(request, 'blog/user_list.html', context)


@manager_required
def organizer_list(request):
    organizers = Organizer.objects.order_by('last_name', 'first_name')
    return render(request, 'blog/organizer_list.html', {'organizers': organizers})


@manager_required
def organizer_create(request):
    form = OrganizerForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        organizer = form.save()
        messages.success(request, 'Организатор создан.')
        return modal_form_response(
            request, None, None,
            success_redirect=reverse('organizer_detail', args=[organizer.id]),
        )
    return modal_form_response(
        request, 'blog/organizer_form.html',
        {'form': form, 'title': 'Новый организатор'},
    )


@manager_required
def organizer_edit(request, organizer_id):
    organizer = get_object_or_404(Organizer, pk=organizer_id)
    form = OrganizerForm(request.POST or None, instance=organizer)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Организатор обновлён.')
        return modal_form_response(
            request, None, None,
            success_redirect=reverse('organizer_detail', args=[organizer.id]),
        )
    return modal_form_response(
        request, 'blog/organizer_form.html',
        {'form': form, 'title': f'Редактировать: {organizer.first_name} {organizer.last_name}', 'submit_label': 'Сохранить'},
    )


@manager_required
def organizer_detail(request, organizer_id):
    organizer = get_object_or_404(Organizer, pk=organizer_id)
    organizations = Organization.objects.filter(organizer_links__organizer=organizer)
    events = organizer.managed_events.prefetch_related('shifts').order_by('-created_at')
    interactions = organizer.interaction_log.select_related('author').order_by('-created_at')
    return render(request, 'blog/organizer_detail.html', {
        'organizer': organizer, 'organizations': organizations,
        'events': events, 'interactions': interactions,
    })


def _new_captcha(request, session_key='captcha_answer'):
    x = random.randint(-10, 10)
    y = random.randint(-10, 10)
    request.session[session_key] = x + y
    return f'{x} + ({y})' if y < 0 else f'{x} + {y}'


def _check_captcha(request, session_key, answer_raw):
    expected = request.session.pop(session_key, None)
    try:
        return expected is not None and int(answer_raw) == expected
    except (TypeError, ValueError):
        return False


# Секции назначения/снятия ролей в настройках: (action, role kind, глагол для сообщений).
ROLE_ACTIONS = {
    'assign_team_lead': ('team_lead', 'assign'),
    'revoke_team_lead': ('team_lead', 'revoke'),
    'assign_manager': ('manager', 'assign'),
    'revoke_manager': ('manager', 'revoke'),
}
ROLE_LABELS = {'team_lead': 'тим-лидера', 'manager': 'менеджера'}


@manager_required
def const_edit(request, const_id):
    """Редактирование константы в модалке (как редактирование мероприятия), с
    двухшаговым подтверждением: капча появляется только когда значение реально
    меняется, и генерируется заново при каждой попытке (нельзя переиспользовать
    подсмотренный пример)."""
    const = get_object_or_404(Const, pk=const_id)

    if request.method == 'POST':
        # ВАЖНО: сохранить исходное значение до form.is_valid() — ModelForm._post_clean()
        # присваивает cleaned_data в instance (construct_instance) уже во время валидации,
        # так что после is_valid() const.value уже равен НОВОМУ значению.
        original_value = const.value
        form = ConstForm(request.POST, instance=const)
        value_changed = form.is_valid() and form.cleaned_data['value'] != original_value

        if not value_changed:
            if form.is_valid():
                # Значение не менялось — сохранять и требовать капчу незачем.
                messages.info(request, 'Значение не изменилось.')
                return modal_form_response(request, None, None, success_redirect=reverse('settings'))
        else:
            session_key = f'captcha_const_{const.id}'
            if 'captcha_answer' in request.POST:
                captcha_ok = _check_captcha(request, session_key, request.POST.get('captcha_answer'))
                if captcha_ok:
                    form.save()
                    messages.success(request, f'Константа «{const.name}» обновлена.')
                    return modal_form_response(request, None, None, success_redirect=reverse('settings'))
                messages.error(request, 'Неверный ответ капчи.')
            # Значение меняется — показываем то же значение формы + новую капчу.
            return modal_form_response(
                request, 'blog/const_form.html',
                {
                    'form': form, 'const': const,
                    'show_captcha': True,
                    'captcha_question': _new_captcha(request, session_key),
                },
                partial_template='blog/partials/_const_form_body.html',
            )

    return modal_form_response(
        request, 'blog/const_form.html',
        {'form': ConstForm(instance=const), 'const': const, 'show_captcha': False},
        partial_template='blog/partials/_const_form_body.html',
    )


@manager_required
def settings_view(request):
    consts = Const.objects.order_by('name')
    seasons = Season.objects.order_by('-start_season')

    if request.method == 'POST' and request.POST.get('action') == 'add_season':
        season_form = SeasonForm(request.POST)
        if season_form.is_valid():
            season_form.save()
            messages.success(request, 'Сезон добавлен.')
            return redirect('settings')
    else:
        season_form = SeasonForm()

    if request.method == 'POST' and request.POST.get('action') == 'close_season':
        from blog.services.stats import close_season
        season = get_object_or_404(Season, pk=request.POST.get('season_id'))
        next_season = close_season(season)
        msg = 'Сезон закрыт: churn зафиксирован в истории, сезонные счётчики обнулены.'
        if next_season:
            msg += ' Создан новый бессрочный сезон — задайте дату его окончания.'
        messages.success(request, msg)
        return redirect('settings')

    if request.method == 'POST' and request.POST.get('action') in ROLE_ACTIONS:
        action = request.POST.get('action')
        kind, verb = ROLE_ACTIONS[action]
        session_key = f'captcha_{action}'

        queryset = (
            roles_service.users_without_role(kind) if verb == 'assign'
            else roles_service.users_with_role(kind)
        )
        role_form = RoleUsersForm(request.POST, queryset=queryset, prefix=action)
        captcha_ok = _check_captcha(request, session_key, request.POST.get('captcha_answer'))

        if not captcha_ok:
            messages.error(request, 'Неверный ответ капчи. Действие отклонено.')
        elif role_form.is_valid():
            users = list(role_form.cleaned_data['users'])
            if verb == 'assign':
                affected = roles_service.assign_role(kind, users, assigned_by=request.user)
                verb_text = 'назначены'
            else:
                affected = roles_service.revoke_role(kind, users)
                verb_text = 'сняты с роли'
            role_word = ROLE_LABELS[kind]
            if affected:
                names = ', '.join(u.full_name for u in affected)
                messages.success(request, f'{role_word.capitalize()} {verb_text}: {names} ({len(affected)}).')
            else:
                messages.info(request, 'Выбранные пользователи уже были в этом статусе — изменений нет.')
            return redirect('settings')
        else:
            messages.error(request, 'Выберите хотя бы одного пользователя.')

    role_sections = {}
    for action, (kind, verb) in ROLE_ACTIONS.items():
        session_key = f'captcha_{action}'
        queryset = (
            roles_service.users_without_role(kind) if verb == 'assign'
            else roles_service.users_with_role(kind)
        )
        role_sections[action] = {
            # prefix обязателен: без него все 4 формы на странице рендерят <select id="id_users">
            # с одинаковым id, и JS chip-виджет находит только первый по document.getElementById.
            'form': RoleUsersForm(queryset=queryset, prefix=action),
            'captcha_question': _new_captcha(request, session_key),
            'users_count': queryset.count(),
        }

    return render(request, 'blog/settings.html', {
        'consts': consts,
        'seasons': seasons,
        'season_form': season_form,
        'role_sections': role_sections,
    })
