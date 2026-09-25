from datetime import datetime, time

from django import forms
from django.utils import timezone
from .models import (
    Event, EventComment, EventShift, Participation, Application, UserComment, EventFeedback,
    Const, Season, Organization, Organizer, User, EventStaff, Location,
    EventCategory, EventCategoryMap, TeamLeader, ExternalExperience,
)


class LoginForm(forms.Form):
    login = forms.CharField(label='Логин', max_length=50)
    password = forms.CharField(label='Пароль', widget=forms.PasswordInput)


class QuickLocationForm(forms.ModelForm):
    """Мини-форма быстрого создания локации прямо из формы мероприятия (без ухода со страницы)."""
    class Meta:
        model = Location
        fields = ['name', 'map_link']
        labels = {'name': 'Название локации', 'map_link': 'Ссылка на карту'}


class LocationForm(forms.ModelForm):
    """Полноценное редактирование локации (список локаций)."""
    class Meta:
        model = Location
        fields = ['name', 'map_link', 'description']
        labels = {'name': 'Название локации', 'map_link': 'Ссылка на карту', 'description': 'Описание'}
        widgets = {
            'map_link': forms.URLInput(attrs={'placeholder': 'https://yandex.ru/maps/…'}),
            'description': forms.Textarea(attrs={'rows': 3}),
        }


class QuickOrganizationForm(forms.ModelForm):
    """Мини-форма быстрого создания организации прямо из формы мероприятия."""
    class Meta:
        model = Organization
        fields = ['name', 'description']
        labels = {'name': 'Название организации', 'description': 'Описание'}
        widgets = {'description': forms.Textarea(attrs={'rows': 2})}


class QuickOrganizerForm(forms.ModelForm):
    """Мини-форма быстрого создания контактного лица прямо из формы мероприятия."""
    class Meta:
        model = Organizer
        fields = ['first_name', 'last_name', 'phone', 'telegram_username']
        labels = {
            'first_name': 'Имя', 'last_name': 'Фамилия',
            'phone': 'Телефон', 'telegram_username': 'Telegram',
        }


class QuickCategoryForm(forms.ModelForm):
    """Мини-форма быстрого создания категории мероприятия прямо из формы мероприятия."""
    class Meta:
        model = EventCategory
        fields = ['name']
        labels = {'name': 'Название категории'}


class TeamLeadChoiceField(forms.ModelMultipleChoiceField):
    """Выбор тим-лидеров: показываем только «Фамилия Имя», без telegram."""
    def label_from_instance(self, obj):
        name = f'{obj.last_name or ""} {obj.first_name or ""}'.strip()
        return name or (obj.telegram_username or str(obj.telegram_id))


class EventForm(forms.ModelForm):
    """Форма создания/редактирования мероприятия.

    На этапе создания (cjm-event-lifecycle.md, этап 2) заполняются только: название, описание,
    локация, организация/организатор, категории, ссылки и тип учёта Добро.ру. Поля закрытия/
    пост-меро (`people_helped`, `internal_comment`, `letter`) появляются только при редактировании
    уже существующего меро (`show_closing_fields`), чтобы не засорять форму создания.
    """
    categories = forms.ModelMultipleChoiceField(
        queryset=EventCategory.objects.order_by('name'),
        required=False,
        label='Категории',
        widget=forms.SelectMultiple,
    )
    team_leads = TeamLeadChoiceField(
        # Только пользователи с активной ролью тим-лидера (таблица team_leaders, revoked_at IS NULL).
        # ВАЖНО: filter(team_leader_roles__revoked_at__isnull=True) через LEFT JOIN даёт
        # ложные совпадения — у пользователей БЕЗ единой записи в team_leaders revoked_at
        # тоже NULL из-за LEFT JOIN, и "IS NULL" совпадает. Поэтому фильтруем по id из
        # TeamLeader напрямую.
        queryset=User.objects.filter(
            id__in=TeamLeader.objects.filter(revoked_at__isnull=True).values('user_id')
        ).order_by('last_name', 'first_name'),
        required=False,
        label='Тим-лидеры',
        widget=forms.SelectMultiple,
    )

    class Meta:
        model = Event
        fields = [
            'title', 'description',
            'organization', 'organizer', 'location',
            'chat_link', 'post_in_chanel_link', 'dobro_ru_link',
            'people_helped', 'internal_comment', 'letter',
        ]
        labels = {
            'title': 'Название',
            'description': 'Описание',
            'location': 'Локация',
            'chat_link': 'Ссылка на чат',
            'post_in_chanel_link': 'Ссылка на пост в канале',
            'dobro_ru_link': 'Ссылка на Добро.ру',
            'organization': 'Организация',
            'organizer': 'Контактное лицо',
            'people_helped': 'Людей получили помощь',
            'internal_comment': 'Внутренний комментарий',
            'letter': 'Шаблон благодарственного письма',
        }
        widgets = {
            'title': forms.TextInput(attrs={'maxlength': 255, 'placeholder': 'Название мероприятия'}),
            'description': forms.Textarea(attrs={'rows': 3, 'maxlength': 250, 'placeholder': 'Кратко опишите мероприятие (до 250 символов)'}),
            'internal_comment': forms.Textarea(attrs={'rows': 3}),
            'chat_link': forms.URLInput(attrs={'placeholder': 'https://t.me/…'}),
            'post_in_chanel_link': forms.URLInput(attrs={'placeholder': 'https://t.me/…'}),
            'dobro_ru_link': forms.URLInput(attrs={'placeholder': 'https://dobro.ru/…'}),
            'people_helped': forms.NumberInput(attrs={'min': 0}),
        }

    # Поля, относящиеся к закрытию/пост-меро — не показываются при создании.
    CLOSING_FIELDS = ('people_helped', 'internal_comment', 'letter')

    def __init__(self, *args, show_closing_fields=False, assigned_by=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.show_closing_fields = show_closing_fields
        self.assigned_by = assigned_by
        # Смены просят только при создании нового меро (правило "нет меро без смен") —
        # при редактировании существующего смены управляются отдельно на event_detail.
        # Несколько смен добавляются прямо в форме создания (chip-виджет на клиенте,
        # см. _event_form_body.html) — JSON-массив приходит в скрытом поле shifts_json,
        # каждый элемент валидируется как отдельная EventShiftForm.
        self.require_shift = not self.instance.pk
        if self.require_shift:
            self.shift_forms = self._build_shift_forms()

        # Пустой вариант "—" для необязательных FK-справочников (иначе Django берёт первый).
        for name in ('organization', 'organizer', 'location', 'letter'):
            if name in self.fields:
                self.fields[name].empty_label = '— не выбрано —'
                self.fields[name].required = False

        # Ранжирование справочников по дате добавления (новые сверху) — чтобы только что
        # созданная через "＋" запись была вверху списка.
        if 'organization' in self.fields:
            self.fields['organization'].queryset = Organization.objects.order_by('-created_at')
        if 'organizer' in self.fields:
            self.fields['organizer'].queryset = Organizer.objects.order_by('-id')
        if 'location' in self.fields:
            self.fields['location'].queryset = Location.objects.order_by('-id')

        if not show_closing_fields:
            for name in self.CLOSING_FIELDS:
                self.fields.pop(name, None)

        if self.instance.pk:
            self.fields['categories'].initial = EventCategory.objects.filter(
                event_map__event=self.instance
            )
            self.fields['team_leads'].initial = User.objects.filter(
                staff_assignments__event=self.instance,
                staff_assignments__staff_role='team_lead',
            )

    def _build_shift_forms(self):
        """Разбирает JSON-массив смен из скрытого поля `shifts_json` (собирается на
        клиенте chip-виджетом, см. _event_form_body.html) в список EventShiftForm —
        каждая смена валидируется отдельно теми же правилами, что и в event_shift_add."""
        import json
        raw = (self.data.get('shifts_json') if self.data else None) or '[]'
        try:
            rows = json.loads(raw)
        except (ValueError, TypeError):
            rows = []
        forms_list = []
        for i, row in enumerate(rows if isinstance(rows, list) else []):
            data = {
                f'shift-{i}-day': row.get('day', ''),
                f'shift-{i}-start_time': row.get('start_time', ''),
                f'shift-{i}-end_time': row.get('end_time', ''),
                f'shift-{i}-volunteer_needed': row.get('volunteer_needed') or 0,
                f'shift-{i}-recruit_status': 'open',
                f'shift-{i}-location': '',
            }
            forms_list.append(EventShiftForm(data, prefix=f'shift-{i}'))
        return forms_list

    def is_valid(self):
        valid = super().is_valid()
        if self.require_shift:
            # Хотя бы одна смена обязательна (аналогично обязательному title); каждая
            # добавленная смена дополнительно валидируется своей формой.
            if not self.shift_forms:
                self.add_error(None, 'Добавьте хотя бы одну смену мероприятия.')
                valid = False
            for shift_form in self.shift_forms:
                if not shift_form.is_valid():
                    valid = False
        return valid

    def save(self, commit=True):
        event = super().save(commit=False)
        # Упрощённая модель: менеджер просто вставляет ссылку на Добро.ру (или оставляет
        # пустой) — kind проставляется автоматически, без выбора shared/unique (db.md 7e
        # по-прежнему хранит это поле, но UI его больше не показывает).
        event.dobro_ru_link_kind = 'unique' if event.dobro_ru_link else 'none'
        if commit:
            event.save()
            self._save_categories(event)
            self._save_team_leads(event)
            if self.require_shift:
                for shift_form in self.shift_forms:
                    shift = shift_form.save(commit=False)
                    shift.event = event
                    shift.save()
        return event

    def _save_categories(self, event):
        selected = self.cleaned_data.get('categories', EventCategory.objects.none())
        EventCategoryMap.objects.filter(event=event).exclude(category__in=selected).delete()
        for category in selected:
            EventCategoryMap.objects.get_or_create(event=event, category=category)

    def _save_team_leads(self, event):
        """Приводит назначения тим-лидеров к ровно выбранному набору пользователей.

        Мероприятие без явно назначенного тим-лидера получает тим-лидером по
        умолчанию самого создающего менеджера (правило "нет меро без ТЛ") — только
        при создании; при редактировании пустой выбор просто снимает всех ТЛ, как раньше.
        """
        selected = list(self.cleaned_data.get('team_leads', User.objects.none()))
        if not selected and self.require_shift and self.assigned_by:
            selected = [self.assigned_by]
        selected_ids = {u.id for u in selected}
        event.staff.filter(staff_role='team_lead').exclude(user_id__in=selected_ids).delete()
        for user in selected:
            EventStaff.objects.get_or_create(event=event, user=user, staff_role='team_lead')
            if not TeamLeader.objects.filter(user=user, revoked_at__isnull=True).exists():
                TeamLeader.objects.create(user=user, assigned_by=self.assigned_by)


class EventShiftForm(forms.ModelForm):
    """Смена мероприятия: одна дата + время начала и окончания (без дат внутри времени).

    Модель хранит start_time/end_time как DateTimeField, но пользователю мы показываем
    отдельную дату и два поля времени, а склеиваем их в datetime уже при сохранении.
    """
    day = forms.DateField(
        label='Дата',
        widget=forms.DateInput(attrs={'type': 'text', 'data-datepicker': '', 'autocomplete': 'off'}, format='%Y-%m-%d'),
    )
    start_time = forms.TimeField(
        label='Время начала',
        widget=forms.TimeInput(attrs={'type': 'text', 'data-timepicker': '', 'autocomplete': 'off'}, format='%H:%M'),
    )
    end_time = forms.TimeField(
        label='Время окончания',
        widget=forms.TimeInput(attrs={'type': 'text', 'data-timepicker': '', 'autocomplete': 'off'}, format='%H:%M'),
    )

    class Meta:
        model = EventShift
        # start_time/end_time НЕ включаем в model-поля: в модели это DateTimeField, а на форме —
        # объявленные выше TimeField. Если оставить их в Meta.fields, ModelForm при _post_clean
        # присвоит объект time в DateTimeField и упадёт на to_python. Склеиваем в save().
        fields = ['day', 'volunteer_needed', 'recruit_status', 'location']
        labels = {
            'volunteer_needed': 'Нужно волонтёров',
            'recruit_status': 'Набор на смену',
            'location': 'Локация',
        }
        widgets = {
            'volunteer_needed': forms.NumberInput(attrs={'min': 0}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['location'].queryset = Location.objects.order_by('-id')
        if self.instance and self.instance.pk:
            # При редактировании существующего дня разложить datetime обратно на дату и время.
            if self.instance.start_time:
                start = timezone.localtime(self.instance.start_time)
                self.initial.setdefault('day', start.date())
                self.initial.setdefault('start_time', start.time())
            if self.instance.end_time:
                self.initial.setdefault('end_time', timezone.localtime(self.instance.end_time).time())
        else:
            # Новая смена — типовые часы мероприятия по умолчанию (10:00-18:00) и
            # сегодняшняя дата, чтобы не заполнять вручную каждый раз (календарь и
            # так визуально показывает сегодня как выбранный день).
            self.initial.setdefault('day', timezone.localdate())
            self.initial.setdefault('start_time', time(10, 0))
            self.initial.setdefault('end_time', time(18, 0))

    def clean(self):
        cleaned = super().clean()
        start = cleaned.get('start_time')
        end = cleaned.get('end_time')
        if start and end and end <= start:
            self.add_error('end_time', 'Время окончания должно быть позже времени начала.')
        return cleaned

    def save(self, commit=True):
        instance = super().save(commit=False)
        day = self.cleaned_data['day']
        tz = timezone.get_current_timezone()
        instance.day = day
        instance.start_time = timezone.make_aware(
            datetime.combine(day, self.cleaned_data['start_time']), tz
        )
        instance.end_time = timezone.make_aware(
            datetime.combine(day, self.cleaned_data['end_time']), tz
        )
        if commit:
            instance.save()
        return instance


class ParticipationForm(forms.ModelForm):
    class Meta:
        model = Participation
        fields = ['status', 'hours', 'full_shift']
        labels = {
            'status': 'Статус явки',
            'hours': 'Часы',
            'full_shift': 'Полная смена',
        }
        widgets = {
            'hours': forms.NumberInput(attrs={'min': 0}),
        }


class ApplicationForm(forms.ModelForm):
    """Заявка на участие через веб (в отличие от Excel-импорта): ФИО/Telegram/телефон
    система уже знает из профиля авторизованного пользователя, поэтому их не спрашивает —
    остаётся выбрать мероприятие и смены.

    Выбор смен — множественный (чипы, как категории у мероприятия): пул вариантов —
    смены, привязанные к выбранному мероприятию с открытым набором. Если у меро всего
    одна смена — выбирать нечего, подаём сразу на неё.
    """
    selected_shifts = forms.ModelMultipleChoiceField(
        queryset=EventShift.objects.none(),
        required=False,
        label='Смены',
        widget=forms.SelectMultiple,
    )

    class Meta:
        model = Application
        fields = ['event', 'functions', 'role_in_event']
        labels = {
            'event': 'Мероприятие',
            'functions': 'Функция',
            'role_in_event': 'Роль',
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.applicant = user
        # Прошедшие мероприятия с ещё открытыми сменами закрываем лениво прямо тут —
        # это самое надёжное место: форма подачи заявки открывается чаще всего и должна
        # гарантированно не предлагать прошедшие меро (нет отдельного планировщика задач).
        for event in Event.objects.filter(shifts__recruit_status='open').distinct():
            event.close_recruit_if_passed()
        # Подать заявку можно только на меро хотя бы с одной сменой с открытым набором —
        # меро, где все смены закрыты (вручную или автоматически), пропадают из выбора.
        self.fields['event'].queryset = Event.objects.filter(
            shifts__recruit_status='open'
        ).distinct().order_by('-created_at')

        # Пул смен для чипов — только смены уже выбранного меро с открытым набором.
        event = self.instance.event if self.instance and self.instance.pk else None
        if 'event' in self.data:
            try:
                event = Event.objects.get(pk=self.data.get('event'))
            except (Event.DoesNotExist, ValueError, TypeError):
                event = None
        if event:
            self.fields['selected_shifts'].queryset = event.shifts.filter(recruit_status='open').order_by('day')
        if self.instance and self.instance.pk:
            self.initial.setdefault('selected_shifts', self.instance.selected_shifts.all())
            # При редактировании существующей заявки мероприятие не меняем — только
            # смены/функцию/роль (иначе теряется смысл "отредактировать свою заявку").
            self.fields['event'].disabled = True


class ApplicationCancelForm(forms.Form):
    """Отмена заявки волонтёром — причина обязательна (доступно только пока до
    мероприятия осталось больше const.late_cancel_hours, проверяется во view)."""
    reason = forms.CharField(
        label='Причина отмены', widget=forms.Textarea(attrs={'rows': 3}),
    )


class ProfileCompletionForm(forms.ModelForm):
    """Дозаполнение аккаунта после входа через Telegram: ФИО обязательно, остальное
    (телефон/почта/дата рождения) — опционально. Нужно, чтобы пользователь мог
    подавать заявки («заполненный аккаунт», см. roles.account_is_complete)."""
    first_name = forms.CharField(label='Имя', max_length=100, required=True)
    last_name = forms.CharField(label='Фамилия', max_length=100, required=False)

    class Meta:
        model = User
        fields = ['first_name', 'last_name', 'phone', 'email', 'birthday']
        widgets = {
            'birthday': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
        }
        labels = {'phone': 'Телефон', 'email': 'Почта', 'birthday': 'Дата рождения'}

    def clean_first_name(self):
        value = (self.cleaned_data.get('first_name') or '').strip()
        if not value:
            raise forms.ValidationError('Укажите имя — без него нельзя подать заявку.')
        return value

    def save(self, commit=True):
        user = super().save(commit=False)
        # Вход через Telegram оставляет registration_status='guest' — после
        # дозаполнения ФИО считаем анкету заполненной.
        user.registration_status = 'registered'
        if commit:
            user.save()
        return user


class ExternalExperienceForm(forms.ModelForm):
    """Внешнее волонтёрское мероприятие (раздел «Мой опыт») — хранится как текст в
    профиле и не влияет на статистику."""
    class Meta:
        model = ExternalExperience
        fields = ['title', 'date']
        widgets = {
            'title': forms.TextInput(attrs={'placeholder': 'Например: Городской субботник'}),
            'date': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
        }
        labels = {'title': 'Название', 'date': 'Дата'}


class ExcelImportForm(forms.Form):
    excel_file = forms.FileField(label='Excel файл', help_text='Загрузите файл .xlsx')


class UserCommentForm(forms.ModelForm):
    class Meta:
        model = UserComment
        fields = ['text', 'event']
        labels = {
            'text': 'Комментарий',
            'event': 'Мероприятие (необязательно)',
        }
        widgets = {
            'text': forms.Textarea(attrs={'rows': 3}),
        }

    def __init__(self, *args, event_queryset=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['event'].required = False
        if event_queryset is not None:
            self.fields['event'].queryset = event_queryset


class EventCommentForm(forms.ModelForm):
    """Комментарий к мероприятию (менеджеры/тим-лидеры) — в UI показывается
    без подписи автора, только текст и дата."""
    class Meta:
        model = EventComment
        fields = ['text']
        labels = {'text': 'Комментарий'}
        widgets = {'text': forms.Textarea(attrs={'rows': 2, 'placeholder': 'Заметка по мероприятию…'})}


class EventFeedbackForm(forms.ModelForm):
    class Meta:
        model = EventFeedback
        fields = ['rating', 'comment']
        labels = {
            'rating': 'Оценка (0-10)',
            'comment': 'Комментарий',
        }
        widgets = {
            'rating': forms.NumberInput(attrs={'min': 0, 'max': 10}),
            'comment': forms.Textarea(attrs={'rows': 3}),
        }


class CaptchaForm(forms.Form):
    """Простая капча-подтверждение для изменения констант (x + y, x,y in [-10, 10])."""
    answer = forms.IntegerField(label='Ответ')

    def __init__(self, *args, question=None, **kwargs):
        super().__init__(*args, **kwargs)
        if question:
            self.fields['answer'].label = f'Решите пример: {question} = ?'


class RoleUsersForm(forms.Form):
    """Выбор пользователей для назначения/снятия роли (настройки, chip-select с поиском)."""
    users = TeamLeadChoiceField(queryset=User.objects.none(), required=True, label='Волонтёры')

    def __init__(self, *args, queryset=None, **kwargs):
        super().__init__(*args, **kwargs)
        if queryset is not None:
            self.fields['users'].queryset = queryset


class ConstForm(forms.ModelForm):
    class Meta:
        model = Const
        fields = ['value']
        labels = {'value': 'Значение'}


class SeasonForm(forms.ModelForm):
    class Meta:
        model = Season
        fields = ['start_season', 'end_season']
        labels = {
            'start_season': 'Начало сезона',
            'end_season': 'Конец сезона (необязательно)',
        }
        widgets = {
            'start_season': forms.DateInput(attrs={'type': 'text', 'data-datepicker': '', 'autocomplete': 'off'}, format='%Y-%m-%d'),
            'end_season': forms.DateInput(attrs={'type': 'text', 'data-datepicker': '', 'autocomplete': 'off'}, format='%Y-%m-%d'),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['end_season'].required = False


class OrganizationForm(forms.ModelForm):
    organizers = forms.ModelMultipleChoiceField(
        queryset=Organizer.objects.order_by('last_name', 'first_name'),
        required=False,
        label='Организаторы',
        widget=forms.SelectMultiple,
    )

    class Meta:
        model = Organization
        fields = ['name', 'description', 'internal_rating', 'comment']
        labels = {
            'name': 'Название',
            'description': 'Описание деятельности',
            'internal_rating': 'Внутренняя оценка',
            'comment': 'Комментарий',
        }
        widgets = {
            'description': forms.Textarea(attrs={'rows': 3}),
            'comment': forms.Textarea(attrs={'rows': 3}),
            'internal_rating': forms.NumberInput(attrs={'min': 0, 'max': 10}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields['organizers'].initial = Organizer.objects.filter(
                organization_links__organization=self.instance
            )

    def save(self, commit=True):
        organization = super().save(commit=commit)
        if commit:
            self._save_organizers(organization)
        return organization

    def save_organizers(self, organization):
        self._save_organizers(organization)

    def _save_organizers(self, organization):
        from blog.models import OrganizerOrganizationLink
        selected = self.cleaned_data.get('organizers', Organizer.objects.none())
        OrganizerOrganizationLink.objects.filter(organization=organization).exclude(
            organizer__in=selected
        ).delete()
        for organizer in selected:
            OrganizerOrganizationLink.objects.get_or_create(organization=organization, organizer=organizer)


class OrganizerForm(forms.ModelForm):
    organizations = forms.ModelMultipleChoiceField(
        queryset=Organization.objects.order_by('name'),
        required=False,
        label='Организации',
        widget=forms.SelectMultiple,
    )

    class Meta:
        model = Organizer
        fields = [
            'first_name', 'last_name', 'phone', 'email',
            'telegram_username', 'VK_username', 'whatsapp', 'comments',
        ]
        labels = {
            'first_name': 'Имя',
            'last_name': 'Фамилия',
            'phone': 'Телефон',
            'email': 'Email',
            'telegram_username': 'Telegram',
            'VK_username': 'VK',
            'whatsapp': 'WhatsApp',
            'comments': 'Комментарии',
        }
        widgets = {
            'comments': forms.Textarea(attrs={'rows': 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields['organizations'].initial = Organization.objects.filter(
                organizer_links__organizer=self.instance
            )

    def save(self, commit=True):
        organizer = super().save(commit=commit)
        if commit:
            self._save_organizations(organizer)
        return organizer

    def save_organizations(self, organizer):
        self._save_organizations(organizer)

    def _save_organizations(self, organizer):
        from blog.models import OrganizerOrganizationLink
        selected = self.cleaned_data.get('organizations', Organization.objects.none())
        OrganizerOrganizationLink.objects.filter(organizer=organizer).exclude(
            organization__in=selected
        ).delete()
        for organization in selected:
            OrganizerOrganizationLink.objects.get_or_create(organizer=organizer, organization=organization)
