import uuid

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager
from django.utils import timezone


class UserManager(BaseUserManager):
    def create_user(self, telegram_id, **extra_fields):
        if not telegram_id:
            raise ValueError('Users must have a telegram_id')
        user = self.model(telegram_id=telegram_id, **extra_fields)
        user.set_unusable_password()  # вход через Telegram по умолчанию
        user.save(using=self._db)
        return user

    def create_web_manager(self, login, password, first_name, last_name, **extra_fields):
        """Служебный путь создания менеджера с веб-входом (management command)."""
        if not login:
            raise ValueError('Manager must have a login')
        telegram_id = extra_fields.pop('telegram_id', None) or f'web:{login}'
        user = self.model(
            telegram_id=telegram_id,
            login=login,
            first_name=first_name,
            last_name=last_name,
            **extra_fields,
        )
        user.set_password(password)
        user.web_credentials_set_at = timezone.now()
        user.save(using=self._db)
        return user


class User(AbstractBaseUser):
    """Центральная сущность системы (волонтёр/тим-лидер/менеджер)."""

    GENDER_CHOICES = [
        ('male', 'Мужской'),
        ('female', 'Женский'),
    ]
    STATUS_CHOICES = [
        ('active', 'Активен'),
        ('inactive', 'Неактивен'),
        ('blacklisted', 'В блэклисте'),
    ]
    REGISTRATION_STATUS_CHOICES = [
        ('guest', 'Гость (первое касание)'),
        ('registered', 'Зарегистрирован'),
    ]

    id = models.AutoField(primary_key=True)
    system_username = models.CharField(max_length=50, blank=True, null=True, db_index=True)
    telegram_id = models.CharField(max_length=50, unique=True, db_index=True)
    telegram_username = models.CharField(max_length=100, blank=True, null=True)
    VK_username = models.CharField(max_length=100, blank=True, null=True)
    first_name = models.CharField(max_length=100, blank=True, null=True)
    last_name = models.CharField(max_length=100, blank=True, null=True)
    email = models.EmailField(blank=True, null=True)
    phone = models.CharField(max_length=20, blank=True, null=True)
    birthday = models.DateField(blank=True, null=True)
    about = models.TextField(blank=True, null=True)
    gender = models.CharField(max_length=10, choices=GENDER_CHOICES, blank=True, null=True)

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='active')
    comment = models.TextField(blank=True, null=True)

    # Прогресс онбординга в TG-боте (tg-bot.md §3.4). Не путать с `status` выше:
    # это про заполненность анкеты, а не про допуск к участию.
    registration_status = models.CharField(
        max_length=20, choices=REGISTRATION_STATUS_CHOICES, default='guest'
    )

    created_at = models.DateTimeField(auto_now_add=True)
    last_event_at = models.DateTimeField(blank=True, null=True)

    # Кэшированная статистика
    total_event_count = models.IntegerField(default=0, validators=[MinValueValidator(0)])
    season_event_count = models.IntegerField(default=0, validators=[MinValueValidator(0)])
    total_hours = models.IntegerField(default=0, validators=[MinValueValidator(0)])
    season_hours = models.IntegerField(default=0, validators=[MinValueValidator(0)])
    churn_score = models.FloatField(default=0)

    # Веб-аутентификация (доп. канал входа).
    # Хеш пароля хранится в унаследованном от AbstractBaseUser поле `password`.
    login = models.CharField(max_length=50, unique=True, blank=True, null=True)
    web_credentials_set_at = models.DateTimeField(blank=True, null=True)

    # Подписка на уведомления бота о новых мероприятиях (Этап 8).
    notify_new_events = models.BooleanField(default=True)

    # Подписка ТЛ/менеджера на уведомления о новых заявках (db.md, правило 7c).
    # При false карточка новой заявки не приходит, но заявка остаётся видна в разделах меро/«Все заявки».
    notify_applications = models.BooleanField(default=True)

    # Токен для публичной ссылки «Скопировать опыт» — открывает read-only блок
    # волонтёрского опыта без входа (раздел «Мой опыт»). Не угадывается перебором id.
    experience_token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False, db_index=True)

    objects = UserManager()

    USERNAME_FIELD = 'telegram_id'
    REQUIRED_FIELDS = []

    class Meta:
        db_table = 'users'
        verbose_name = 'Пользователь'
        verbose_name_plural = 'Пользователи'

    def __str__(self):
        return f"{self.first_name} {self.last_name} ({self.telegram_id})"

    @property
    def full_name(self):
        return f"{self.first_name or ''} {self.last_name or ''}".strip() or self.telegram_username or self.telegram_id

    @property
    def is_active(self):
        return self.status != 'blacklisted'

    def is_active_manager(self):
        return self.manager_roles.filter(revoked_at__isnull=True).exists()

    def is_active_team_leader(self):
        return self.team_leader_roles.filter(revoked_at__isnull=True).exists()

    def is_registered(self):
        return self.registration_status == 'registered'


class Manager(models.Model):
    """История выдачи и отзыва ролей менеджеров."""
    id = models.AutoField(primary_key=True)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='manager_roles')
    assigned_at = models.DateTimeField(auto_now_add=True)
    revoked_at = models.DateTimeField(blank=True, null=True)
    assigned_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='assigned_managers')

    total_events = models.IntegerField(default=0, validators=[MinValueValidator(0)])
    season_events = models.IntegerField(default=0, validators=[MinValueValidator(0)])

    class Meta:
        db_table = 'managers'
        verbose_name = 'Менеджер'
        verbose_name_plural = 'Менеджеры'


class TeamLeader(models.Model):
    """История выдачи и отзыва ролей тим-лидеров."""
    id = models.AutoField(primary_key=True)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='team_leader_roles')
    assigned_at = models.DateTimeField(auto_now_add=True)
    revoked_at = models.DateTimeField(blank=True, null=True)
    assigned_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='assigned_team_leaders')

    total_events = models.IntegerField(default=0, validators=[MinValueValidator(0)])
    season_events = models.IntegerField(default=0, validators=[MinValueValidator(0)])

    class Meta:
        db_table = 'team_leaders'
        verbose_name = 'Тим-лидер'
        verbose_name_plural = 'Тим-лидеры'


class Organization(models.Model):
    """Юридические лица или структурные подразделения."""
    id = models.AutoField(primary_key=True)
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    events_count = models.IntegerField(default=0, validators=[MinValueValidator(0)])
    volunteers_count = models.IntegerField(default=0, validators=[MinValueValidator(0)])
    shifts_count = models.IntegerField(default=0, validators=[MinValueValidator(0)])
    internal_rating = models.SmallIntegerField(
        blank=True, null=True,
        validators=[MinValueValidator(0), MaxValueValidator(10)],
    )
    comment = models.TextField(blank=True, null=True)

    class Meta:
        db_table = 'organizations'
        verbose_name = 'Организация'
        verbose_name_plural = 'Организации'

    def __str__(self):
        return self.name


class Organizer(models.Model):
    """Контактные лица организаций."""
    id = models.AutoField(primary_key=True)
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='organizer_profile')
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    phone = models.CharField(max_length=20, blank=True, null=True)
    email = models.EmailField(blank=True, null=True)
    telegram_username = models.CharField(max_length=100, blank=True, null=True)
    VK_username = models.CharField(max_length=100, blank=True, null=True)
    whatsapp = models.CharField(max_length=50, blank=True, null=True)
    comments = models.TextField(blank=True, null=True)

    class Meta:
        db_table = 'organizers'
        verbose_name = 'Контактное лицо'
        verbose_name_plural = 'Контактные лица'

    def __str__(self):
        return f"{self.first_name} {self.last_name}"


class OrganizerOrganizationLink(models.Model):
    """Связь организатор-организация (Many-to-Many)."""
    id = models.AutoField(primary_key=True)
    organizer = models.ForeignKey(Organizer, on_delete=models.CASCADE, related_name='organization_links')
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='organizer_links')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'organizer_organization_link'
        unique_together = ('organizer', 'organization')
        verbose_name = 'Связь организатора и организации'
        verbose_name_plural = 'Связи организаторов и организаций'


class OrganizerInteractionLog(models.Model):
    """История контактов менеджеров с организатором."""
    CONTACT_CHOICES = [
        ('call', 'Звонок'),
        ('message', 'Сообщение'),
        ('meeting', 'Встреча'),
    ]

    id = models.AutoField(primary_key=True)
    organizer = models.ForeignKey(Organizer, on_delete=models.CASCADE, related_name='interaction_log')
    author = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='organizer_interactions')
    contact_type = models.CharField(max_length=20, choices=CONTACT_CHOICES)
    note = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'organizer_interaction_log'
        verbose_name = 'Лог взаимодействия с организатором'
        verbose_name_plural = 'Логи взаимодействия с организаторами'


class Location(models.Model):
    """Справочник локаций."""
    id = models.AutoField(primary_key=True)
    name = models.CharField(max_length=255, unique=True)
    map_link = models.URLField(blank=True, null=True, verbose_name='Ссылка на карту')
    description = models.TextField(blank=True, null=True, verbose_name='Описание')

    class Meta:
        db_table = 'locations'
        verbose_name = 'Локация'
        verbose_name_plural = 'Локации'

    def __str__(self):
        return self.name


class EventCategory(models.Model):
    """Справочник категорий мероприятий."""
    id = models.AutoField(primary_key=True)
    name = models.CharField(max_length=100, unique=True)

    class Meta:
        db_table = 'event_categories'
        verbose_name = 'Категория мероприятия'
        verbose_name_plural = 'Категории мероприятий'

    def __str__(self):
        return self.name


class DobroRuSharedLink(models.Model):
    """Общая ссылка на Добро.ру для нескольких мелких мероприятий сразу
    (db.md §dobro_ru_shared_links) — чтобы не заводить отдельную ссылку на каждое."""
    LINK_TYPE_CHOICES = [
        ('event', 'Мероприятие'),
        ('organization', 'Организация'),
        ('project', 'Проект'),
    ]

    id = models.AutoField(primary_key=True)
    title = models.CharField(max_length=255)
    url = models.URLField()
    link_type = models.CharField(max_length=20, choices=LINK_TYPE_CHOICES)
    period_hint = models.CharField(max_length=100, blank=True, null=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'dobro_ru_shared_links'
        verbose_name = 'Общая ссылка Добро.ру'
        verbose_name_plural = 'Общие ссылки Добро.ру'

    def __str__(self):
        return self.title


class ThankYouLetterTemplate(models.Model):
    """Шаблоны благодарственных писем."""
    id = models.AutoField(primary_key=True)
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    pattern_link = models.TextField(blank=True, null=True)

    class Meta:
        db_table = 'thank_you_letter_templates'
        verbose_name = 'Шаблон благодарственного письма'
        verbose_name_plural = 'Шаблоны благодарственных писем'

    def __str__(self):
        return self.title


class Const(models.Model):
    """Key-value справочник настраиваемых бизнес-параметров."""
    id = models.AutoField(primary_key=True)
    name = models.CharField(max_length=100, unique=True)
    value = models.FloatField()
    description = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'const'
        verbose_name = 'Константа'
        verbose_name_plural = 'Константы'

    def __str__(self):
        return f"{self.name} = {self.value}"


class Season(models.Model):
    """Периоды проведения ПГАС."""
    id = models.AutoField(primary_key=True)
    created_at = models.DateTimeField(auto_now_add=True)
    start_season = models.DateField()
    end_season = models.DateField(blank=True, null=True)

    class Meta:
        db_table = 'seasons'
        verbose_name = 'Сезон'
        verbose_name_plural = 'Сезоны'

    def __str__(self):
        return f"Season: {self.start_season} - {self.end_season or '...'}"


class Event(models.Model):
    """Основная сущность события.

    Набор заявок (recruit_status) и нужное количество волонтёров (volunteer_needed)
    живут на уровне смены (EventShift), а не мероприятия — у мероприятия из нескольких
    смен каждая смена сама решает, открыт у неё набор или нет. На Event остаётся только
    общий event_status жизненного цикла (upcoming/ongoing/passed/finished)."""
    STATUS_CHOICES = [
        ('upcoming', 'Предстоит'),
        ('ongoing', 'Идёт'),
        ('passed', 'Прошло'),
        ('finished', 'Завершено'),
    ]
    DOBRO_LINK_KIND_CHOICES = [
        ('none', 'Нет ссылки'),
        ('shared', 'Общая ссылка'),
        ('unique', 'Уникальная ссылка'),
    ]

    id = models.AutoField(primary_key=True)
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    location = models.ForeignKey(Location, on_delete=models.SET_NULL, null=True, blank=True, related_name='events')
    chat_link = models.URLField(blank=True, null=True)
    post_in_chanel_link = models.URLField(blank=True, null=True)

    # Учёт часов на Добро.ру (db.md, правило 7e): none — не ведётся, unique — своя ссылка,
    # shared — общая ссылка на несколько мелких меро (DobroRuSharedLink).
    dobro_ru_link_kind = models.CharField(max_length=20, choices=DOBRO_LINK_KIND_CHOICES, default='none')
    dobro_ru_link = models.URLField(blank=True, null=True)
    dobro_ru_shared_link = models.ForeignKey(
        DobroRuSharedLink, on_delete=models.SET_NULL, null=True, blank=True, related_name='events'
    )
    dobro_ru_hours_logged = models.BooleanField(default=False)
    dobro_ru_logged_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='dobro_ru_logged_events')

    event_status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='upcoming')

    organization = models.ForeignKey(Organization, on_delete=models.SET_NULL, null=True, blank=True, related_name='events')
    organizer = models.ForeignKey(Organizer, on_delete=models.SET_NULL, null=True, blank=True, related_name='managed_events')

    created_at = models.DateTimeField(auto_now_add=True)
    published_at = models.DateTimeField(blank=True, null=True)
    # Момент публикации поста в Telegram-канале — отдельно от published_at (анонс на
    # платформе), для графика тайминга заявок с двумя отсечками (db.md, reports.md).
    post_published_at = models.DateTimeField(blank=True, null=True)
    # Момент отправки рассылки-оповещения подписанным волонтёрам (кнопка «Сделать
    # рассылку» — отдельно от «Опубликовать»: публикация открывает подачу заявок,
    # рассылка лишь шлёт уведомления и может быть выполнена позже/повторно).
    broadcast_sent_at = models.DateTimeField(blank=True, null=True)
    # Токен для публичной («поделиться») ссылки на мероприятие — доступной без входа.
    public_token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False, db_index=True)
    people_helped = models.IntegerField(blank=True, null=True, validators=[MinValueValidator(0)])
    internal_comment = models.TextField(blank=True, null=True)

    letter = models.ForeignKey(ThankYouLetterTemplate, on_delete=models.SET_NULL, null=True, blank=True, related_name='events')

    class Meta:
        db_table = 'events'
        verbose_name = 'Мероприятие'
        verbose_name_plural = 'Мероприятия'

    def __str__(self):
        return self.title

    def compute_status(self):
        """Статус мероприятия по датам смен (`shifts`), а не по хранимому полю:

        - «Завершено» — только если мероприятие явно закрыто менеджером кнопкой «Завершить»
          (event_status='finished', см. views.event_close). Это единственное ручное
          состояние — автоматика по датам его не выставляет и не может отменить.
        - «Идёт» — хотя бы одна смена мероприятия приходится на сегодняшнюю дату.
        - «Прошло» — последняя смена раньше сегодняшней даты, но менеджер ещё не закрыл
          мероприятие явно (даты кончились, а чеклист закрытия не пройден).
        - «Предстоит» — первая смена позже сегодняшней даты.
        - Если у мероприятия ещё нет смен — используем хранимое `event_status` как есть
          (обычно 'upcoming' сразу после создания).
        """
        if self.event_status == 'finished':
            return 'finished'

        shifts = list(self.shifts.all()) if self.pk else []
        if not shifts:
            return self.event_status

        today = timezone.now().date()
        shift_dates = [s.day for s in shifts]
        first_day, last_day = min(shift_dates), max(shift_dates)

        if today in shift_dates:
            return 'ongoing'
        if last_day < today:
            return 'passed'
        if first_day > today:
            return 'upcoming'
        # Между первой и последней сменой, но сегодняшней даты среди смен нет
        # (напр. многодневное меро с "окном" без активности сегодня) — считаем идущим.
        return 'ongoing'

    def compute_status_display(self):
        return dict(self.STATUS_CHOICES).get(self.compute_status(), self.compute_status())

    def close_recruit_if_passed(self):
        """Автоматически закрывает набор на все ещё открытые смены, если мероприятие уже
        прошло/завершено — подать заявку на прошедшее меро бессмысленно. Вызывается лениво
        в местах просмотра/выбора мероприятия (нет отдельного планировщика задач).
        Возвращает True, если хотя бы одна смена была закрыта."""
        if self.compute_status() not in ('passed', 'finished'):
            return False
        updated = self.shifts.filter(recruit_status='open').update(recruit_status='closed')
        return updated > 0


class EventCategoryMap(models.Model):
    """Связь мероприятий с категориями (Many-to-Many)."""
    id = models.AutoField(primary_key=True)
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name='category_map')
    category = models.ForeignKey(EventCategory, on_delete=models.CASCADE, related_name='event_map')

    class Meta:
        db_table = 'event_category_map'
        unique_together = ('event', 'category')
        verbose_name = 'Категория мероприятия (связь)'
        verbose_name_plural = 'Категории мероприятий (связи)'


class EventShift(models.Model):
    """Смена мероприятия: конкретная дата и временной слот со своим набором заявок.

    Мероприятие может состоять из нескольких смен (в один день или в разные дни) —
    у каждой смены свой recruit_status/volunteer_needed, но тим-лидеры, менеджеры,
    организаторы и т.д. полностью наследуются от мероприятия целиком."""
    RECRUIT_STATUS_CHOICES = [
        ('open', 'Открыт'),
        ('closed', 'Закрыт'),
    ]

    id = models.AutoField(primary_key=True)
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name='shifts')
    day = models.DateField()
    start_time = models.DateTimeField()
    end_time = models.DateTimeField()
    volunteer_needed = models.IntegerField(default=0, validators=[MinValueValidator(0)])
    recruit_status = models.CharField(max_length=20, choices=RECRUIT_STATUS_CHOICES, default='open')
    location = models.ForeignKey(Location, on_delete=models.SET_NULL, null=True, blank=True, related_name='event_shifts')

    class Meta:
        db_table = 'event_shifts'
        verbose_name = 'Смена мероприятия'
        verbose_name_plural = 'Смены мероприятий'
        indexes = [
            models.Index(fields=['event']),
        ]

    def __str__(self):
        return f"{self.event.title} - {self.day}"

    def compute_status(self):
        """Статус смены по датам (независимо от общего статуса мероприятия):
        idёт/предстоит/прошла — используется для подсветки строк в таблицах смен."""
        today = timezone.now().date()
        if self.day == today:
            return 'ongoing'
        if self.day < today:
            return 'passed'
        return 'upcoming'


class EventStaff(models.Model):
    """Назначение менеджеров и тим-лидеров на конкретные события."""
    ROLE_CHOICES = [
        ('manager', 'Менеджер'),
        ('team_lead', 'Тим-лидер'),
    ]

    id = models.AutoField(primary_key=True)
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name='staff')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='staff_assignments')
    staff_role = models.CharField(max_length=20, choices=ROLE_CHOICES)
    assigned_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'event_staff'
        verbose_name = 'Персонал мероприятия'
        verbose_name_plural = 'Персонал мероприятий'
        indexes = [
            models.Index(fields=['event', 'staff_role']),
        ]


class Application(models.Model):
    """Входящие запросы на участие. Могут быть от незарегистрированных пользователей."""

    STATUS_CHOICES = [
        ('new', 'Новая'),
        ('approved', 'Одобрена'),
        ('rejected', 'Отклонена/резерв'),
        ('cancelled', 'Отменена волонтёром'),
    ]

    id = models.AutoField(primary_key=True)
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name='applications')
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='applications')

    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100, blank=True, null=True)
    telegram_username = models.CharField(max_length=100, blank=True, null=True)
    phone = models.CharField(max_length=20, blank=True, null=True)

    functions = models.CharField(max_length=255, blank=True, null=True)
    # Человекочитаемая сводка смен — как раньше, для совместимости с Excel-импортом
    # (db.md: колонка "Смена"), заполняется автоматически из selected_shifts при подаче с сайта.
    shifts = models.CharField(max_length=255, blank=True, null=True)
    # Реальная связь с конкретными сменами меро (выбор чипами на веб-форме подачи заявки) —
    # используется при approve, чтобы создать participations только по выбранным сменам,
    # а не по всем сменам меро. Пусто у заявок из Excel-импорта (там только текст в `shifts`).
    selected_shifts = models.ManyToManyField(EventShift, blank=True, related_name='applications')
    role_in_event = models.CharField(max_length=255, blank=True, null=True)
    user_status = models.CharField(max_length=100, blank=True, null=True)

    submitted_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    assigned_at = models.DateTimeField(blank=True, null=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='new')

    assigned_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='processed_applications')

    # Отмена заявки самим волонтёром (веб) — доступна только пока до мероприятия
    # осталось больше const.late_cancel_hours; причина обязательна.
    cancelled_at = models.DateTimeField(blank=True, null=True)
    cancel_reason = models.TextField(blank=True, null=True)

    class Meta:
        db_table = 'applications'
        verbose_name = 'Заявка'
        verbose_name_plural = 'Заявки'
        indexes = [
            models.Index(fields=['event', 'status']),
        ]

    def __str__(self):
        return f"Application #{self.id} for {self.event.title}"


class ApplicationShiftDecision(models.Model):
    """Решение (одобрить/резерв) по заявке — ОТДЕЛЬНО на каждую смену, если заявка
    подана сразу на несколько (`Application.selected_shifts`). Нужно, чтобы принять/
    отклонить заявку на одну смену не затрагивало статус той же заявки на другие
    смены — раньше был единственный общий `Application.status` на все смены сразу."""
    STATUS_CHOICES = [
        ('new', 'Новая'),
        ('approved', 'Одобрена'),
        ('rejected', 'Отклонена/резерв'),
    ]

    id = models.AutoField(primary_key=True)
    application = models.ForeignKey(Application, on_delete=models.CASCADE, related_name='shift_decisions')
    shift = models.ForeignKey(EventShift, on_delete=models.CASCADE, related_name='application_decisions')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='new')
    assigned_at = models.DateTimeField(blank=True, null=True)
    assigned_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='processed_shift_decisions')

    class Meta:
        db_table = 'application_shift_decisions'
        unique_together = ('application', 'shift')
        verbose_name = 'Решение по смене заявки'
        verbose_name_plural = 'Решения по сменам заявок'


class ApplicationChangeLog(models.Model):
    """Лента изменений заявок мероприятия — заполняется при повторном Excel-импорте:
    заявка идентифицируется как (человек, мероприятие), повторная загрузка не создаёт
    дубль, а ОБНОВЛЯЕТ существующую запись, и здесь фиксируется, что именно изменилось
    ('этот изменил заявку с такого-то наполнения на такое', 'этот отказался', 'эти
    появились'). Тим-лидер/менеджер отмечает ленту прочитанной кнопкой — очищается
    целиком. После завершения мероприятия лента больше не показывается (не очищается
    физически, просто скрыта в UI — see event_detail context: is_finished)."""
    KIND_CHOICES = [
        ('changed', 'Изменена'),
        ('withdrawn', 'Отозвана'),
        ('new', 'Новая'),
    ]

    id = models.AutoField(primary_key=True)
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name='application_change_logs')
    application = models.ForeignKey(Application, on_delete=models.CASCADE, related_name='change_logs', null=True, blank=True)
    kind = models.CharField(max_length=20, choices=KIND_CHOICES)
    text = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'application_change_logs'
        verbose_name = 'Запись об изменении заявки'
        verbose_name_plural = 'Записи об изменениях заявок'
        ordering = ['-created_at']


class Participation(models.Model):
    """Подтвержденные участия."""
    STATUS_CHOICES = [
        ('attended', 'Присутствовал'),
        ('absent_excused', 'Отсутствовал (уважительно)'),
        ('absent_unexcused', 'Отсутствовал (без предупреждения)'),
    ]

    id = models.AutoField(primary_key=True)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='participations')
    event_day = models.ForeignKey(EventShift, on_delete=models.CASCADE, related_name='participations')
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default='attended')
    # Отработал ли волонтёр полную смену (db.md, правило 7a) — не влияет на часы,
    # отдельная отметка добросовестного участия, проставляется тим-лидером вместе со статусом.
    full_shift = models.BooleanField(default=False)
    hours = models.IntegerField(default=0, validators=[MinValueValidator(0)])
    created_at = models.DateTimeField(auto_now_add=True)

    # Отметка, что запрос "оцени мероприятие" уже отправлен (tg-bot.md §4.6) —
    # чтобы фоновая задача не слала его повторно при каждом проходе.
    feedback_requested_at = models.DateTimeField(blank=True, null=True)

    # Ручная простановка часов на Добро.ру менеджером (db.md, правило 7e) — построчно по волонтёру.
    dobro_ru_logged = models.BooleanField(default=False)
    dobro_ru_logged_at = models.DateTimeField(blank=True, null=True)
    dobro_ru_logged_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name='dobro_ru_logged_participations'
    )

    class Meta:
        db_table = 'participations'
        verbose_name = 'Участие'
        verbose_name_plural = 'Участия'
        indexes = [
            models.Index(fields=['user', 'event_day']),
        ]


class ChurnHistory(models.Model):
    """Снимок churn_score на момент завершения сезона."""
    id = models.AutoField(primary_key=True)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='churn_history')
    season = models.ForeignKey(Season, on_delete=models.CASCADE, related_name='churn_history')
    churn_score = models.FloatField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'churn_history'
        verbose_name = 'История чарна'
        verbose_name_plural = 'История чарна'
        indexes = [
            models.Index(fields=['user', 'season']),
        ]


class EventComment(models.Model):
    """Внутренние комментарии к мероприятию от менеджеров/тим-лидеров (не путать
    с Event.internal_comment — то одно поле для заметок, а это лента реплик от
    разных авторов). В UI показываются без подписи автора — только текст и дата."""
    id = models.AutoField(primary_key=True)
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name='comments')
    author = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='authored_event_comments')
    text = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'event_comments'
        verbose_name = 'Комментарий к мероприятию'
        verbose_name_plural = 'Комментарии к мероприятиям'
        ordering = ['created_at']


class UserComment(models.Model):
    """Внутренние комментарии к волонтёрам от менеджеров/тим-лидеров."""
    id = models.AutoField(primary_key=True)
    target_user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='received_comments')
    author = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='authored_comments')
    event = models.ForeignKey(Event, on_delete=models.SET_NULL, null=True, blank=True, related_name='user_comments')
    text = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'user_comments'
        verbose_name = 'Комментарий к волонтёру'
        verbose_name_plural = 'Комментарии к волонтёрам'


class EventFeedback(models.Model):
    """Отзывы о мероприятии."""
    id = models.AutoField(primary_key=True)
    author = models.ForeignKey(User, on_delete=models.CASCADE, related_name='feedbacks')
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name='feedbacks')
    rating = models.SmallIntegerField(validators=[MinValueValidator(0), MaxValueValidator(10)])  # 0-10
    comment = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'event_feedbacks'
        verbose_name = 'Отзыв'
        verbose_name_plural = 'Отзывы'


class EventRequirement(models.Model):
    """Справочник требований к волонтерам."""
    id = models.AutoField(primary_key=True)
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)

    class Meta:
        db_table = 'event_requirements'
        verbose_name = 'Требование'
        verbose_name_plural = 'Требования'

    def __str__(self):
        return self.title


class EventRequirementMap(models.Model):
    """Связь требований с мероприятиями."""
    id = models.AutoField(primary_key=True)
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name='requirement_map')
    requirement = models.ForeignKey(EventRequirement, on_delete=models.CASCADE, related_name='event_map')

    class Meta:
        db_table = 'event_requirement_map'
        unique_together = ('event', 'requirement')
        verbose_name = 'Требование мероприятия (связь)'
        verbose_name_plural = 'Требования мероприятий (связи)'


class BotOutbox(models.Model):
    """Очередь исходящих сообщений в Telegram (диспетчер уведомлений, Этап 8).

    Сообщения кладутся сюда из веб-приложения/сервисов и отправляются процессом
    бота (management command `run_bot`) с ретраями при сбоях Telegram API.
    """
    STATUS_CHOICES = [
        ('pending', 'Ожидает отправки'),
        ('sent', 'Отправлено'),
        ('failed', 'Ошибка (исчерпаны попытки)'),
    ]

    id = models.AutoField(primary_key=True)
    chat_id = models.CharField(max_length=50, db_index=True)
    text = models.TextField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    attempts = models.IntegerField(default=0)
    last_error = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        db_table = 'bot_outbox'
        verbose_name = 'Исходящее сообщение бота'
        verbose_name_plural = 'Исходящие сообщения бота'
        indexes = [
            models.Index(fields=['status', 'created_at']),
        ]


class PendingButtonRemoval(models.Model):
    """Очередь на снятие inline-клавиатуры у отправленных ботом сообщений.

    Любое сообщение бота с inline-кнопками (подать заявку, одобрить/резерв, оценка
    и т.д.) должно "остывать" — кнопки не должны быть нажимаемы бесконечно (пользователь
    иначе может нажать на кнопку многомесячной давности с уже неактуальным состоянием).
    При отправке такого сообщения хендлер создаёт здесь запись с `remove_at = now + N`;
    периодический обходчик в `tg_bot/notifier.py` (тот же цикл, что и `bot_outbox`/feedback)
    снимает разметку (`edit_message_reply_markup(reply_markup=None)`) и удаляет запись.
    Переживает рестарт бота — расписание живёт в БД, а не в памяти процесса.
    """
    id = models.AutoField(primary_key=True)
    chat_id = models.CharField(max_length=50, db_index=True)
    message_id = models.BigIntegerField()
    remove_at = models.DateTimeField(db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'pending_button_removals'
        verbose_name = 'Отложенное снятие кнопок'
        verbose_name_plural = 'Отложенные снятия кнопок'
        indexes = [
            models.Index(fields=['remove_at']),
        ]


class ExternalExperience(models.Model):
    """Внешнее волонтёрское мероприятие, не учтённое в системе центра (раздел «Мой опыт»).

    Хранится как простой текст в личном профиле волонтёра и НЕ участвует в статистике
    (никаких Participation/часов/пересчётов) — это самозаявленный опыт «извне»."""
    id = models.AutoField(primary_key=True)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='external_experiences')
    title = models.CharField(max_length=255, verbose_name='Название')
    date = models.DateField(blank=True, null=True, verbose_name='Дата')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'external_experiences'
        verbose_name = 'Внешний опыт'
        verbose_name_plural = 'Внешний опыт'
        ordering = ['-date', '-created_at']

    def __str__(self):
        return f'{self.title} ({self.user_id})'


class TelegramAuthToken(models.Model):
    """Одноразовая ссылка для входа/авто-регистрации через Telegram-бота (браузерный
    вход вне Mini App): пользователь открывает бота по deep-link `?start=weblogin`,
    бот создаёт эту запись и присылает ссылку `/auth/tg/<token>/`, действующую 5 минут.
    Переход по ссылке логинит пользователя в веб-сессию и помечает токен использованным."""
    token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False, db_index=True)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='auth_tokens')
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(db_index=True)
    used_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        db_table = 'telegram_auth_tokens'
        verbose_name = 'Токен входа через Telegram'
        verbose_name_plural = 'Токены входа через Telegram'

    def is_valid(self) -> bool:
        return self.used_at is None and timezone.now() < self.expires_at


class UserRequirementException(models.Model):
    """Исключения для волонтеров по требованиям."""
    id = models.AutoField(primary_key=True)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='requirement_exceptions')
    requirement = models.ForeignKey(EventRequirement, on_delete=models.CASCADE, related_name='user_exceptions')
    reason = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'user_requirement_exceptions'
        unique_together = ('user', 'requirement')
        verbose_name = 'Исключение пользователя'
        verbose_name_plural = 'Исключения пользователей'
