"""Полноценный демонстрационный датасет для страницы отчётов.

Моделирует картину «прошёл целый сезон»: ~30 завершённых мероприятий, ~80 волонтёров,
~250 заявок с участиями и явками, плюс актуальная оперативка — 2 мероприятия с открытым
набором и заявками и 1 предстоящее мероприятие, ожидающее открытия набора.

Покрывает все кейсы отчётов (reports_template.md / reports.md §7–8):
- сезоны Зима/Весна/Лето/Осень (не пересекаются);
- разные категории (график распределения), многодневные меро (смены);
- полный набор / недобор (<0.8) / высокий % отказов (>0.5) / низкий рейтинг (<6);
- неявки без предупреждения и churn (на грани blacklist / в blacklist);
- новые и вернувшиеся волонтёры (retention);
- Добро.ру трёх типов (none / shared / unique) для отчёта простановки часов;
- published_at / post_published_at и разброс submitted_at для тайминга заявок.

Запуск:  python manage.py seed_report_demo
Флаг --keep — не удалять существующие данные (по умолчанию чистит демо-сущности).
"""
import random
from datetime import date, datetime, time, timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from blog.models import (
    Application, Const, DobroRuSharedLink, Event, EventCategory, EventCategoryMap,
    EventFeedback, EventShift, EventStaff, Location, Manager, Organization,
    Organizer, OrganizerOrganizationLink, Participation, Season, TeamLeader, User,
)

RND = random.Random(20260708)  # фиксированный seed — воспроизводимый датасет

FIRST_NAMES_M = ['Александр', 'Дмитрий', 'Максим', 'Иван', 'Никита', 'Артём', 'Егор', 'Илья', 'Кирилл', 'Михаил', 'Данил', 'Тимофей', 'Роман', 'Владимир', 'Пётр']
FIRST_NAMES_F = ['Анна', 'Мария', 'Елена', 'Дарья', 'София', 'Виктория', 'Полина', 'Алиса', 'Ксения', 'Екатерина', 'Валерия', 'Арина', 'Вероника', 'Ольга', 'Татьяна']
LAST_NAMES = ['Иванов', 'Смирнов', 'Кузнецов', 'Попов', 'Соколов', 'Лебедев', 'Козлов', 'Новиков', 'Морозов', 'Петров', 'Волков', 'Соловьёв', 'Васильев', 'Зайцев', 'Павлов', 'Семёнов', 'Голубев', 'Виноградов', 'Богданов', 'Воробьёв']

ORG_NAMES = [
    ('Городской парк культуры', 'Парк, проводящий культурно-массовые и экологические акции.'),
    ('Фонд «Тёплый дом»', 'Помощь пожилым людям и семьям в трудной ситуации.'),
    ('Спортивная федерация города', 'Организация массовых спортивных мероприятий и забегов.'),
    ('Эко-движение «Чистый берег»', 'Уборка территорий, раздельный сбор, экопросвещение.'),
    ('Библиотечная сеть', 'Культурные и образовательные мероприятия, лектории.'),
    ('Приют для животных «Лапа»', 'Помощь бездомным животным, пристройство, уход.'),
]

CATEGORIES = ['Спорт', 'Культура', 'Экология', 'Образование', 'Социальная помощь', 'Помощь животным']

EVENT_TITLES = [
    'Городской марафон', 'Субботник в парке', 'Фестиваль науки', 'День донора',
    'Уборка набережной', 'Книжная ярмарка', 'Помощь приюту «Лапа»', 'Лекторий выходного дня',
    'Экоквест для школьников', 'Концерт под открытым небом', 'Забег «Осенний ветер»',
    'Раздача горячих обедов', 'Мастер-класс по переработке', 'Ночь музеев',
    'Велопарад', 'Посадка деревьев', 'Турнир по мини-футболу', 'Ярмарка мастеров',
    'Помощь ветеранам', 'Детский спортивный праздник', 'Форум волонтёров',
    'Сбор кормов для приюта', 'Открытая лекция по экологии', 'Зимний забег',
    'Благотворительный концерт', 'Уборка лесопарка', 'Фестиваль здоровья',
    'Праздник двора', 'Марафон чтения', 'Экскурсия для подопечных фонда',
]


class Command(BaseCommand):
    help = 'Создаёт полноценный демо-датасет для отчётов (сезоны, ~30 меро, ~80 волонтёров, ~250 заявок)'

    def add_arguments(self, parser):
        parser.add_argument('--keep', action='store_true', help='Не удалять существующие данные')

    @transaction.atomic
    def handle(self, *args, **options):
        if not options['keep']:
            self._wipe()

        self._ensure_consts()
        seasons = self._create_seasons()
        locations = self._ensure_locations()
        categories = self._ensure_categories()
        orgs, organizers = self._create_orgs()
        manager, team_leads = self._ensure_staff_users()
        volunteers = self._create_volunteers(seasons)
        shared_links = self._create_shared_links()

        # Основной массив: завершённые мероприятия «прошлого сезона» (весна/лето/осень).
        finished_seasons = [s for s in seasons if s['key'] in ('spring', 'summer', 'autumn')]
        finished_count = self._create_finished_events(
            30, finished_seasons, locations, categories, orgs, organizers,
            manager, team_leads, volunteers, shared_links,
        )

        # Оперативка: 2 меро с открытым набором + заявки, 1 предстоящее (набор ещё не открыт).
        self._create_open_recruiting_events(2, locations, categories, orgs, organizers, manager, team_leads, volunteers)
        self._create_upcoming_event(locations, categories, orgs, organizers, manager, team_leads)

        self._recalc_user_stats(volunteers)

        self.stdout.write(self.style.SUCCESS(
            f'\nГотово. Сезонов: {len(seasons)}, завершённых меро: {finished_count}, '
            f'волонтёров: {len(volunteers)}, менеджер: {manager.full_name}, '
            f'тим-лидеров: {len(team_leads)}.\n'
            f'Заявок всего: {Application.objects.count()}, участий: {Participation.objects.count()}.'
        ))

    # ------------------------------------------------------------------ wipe
    def _wipe(self):
        self.stdout.write('Очистка прежних данных…')
        # Порядок: сначала зависимые, потом справочники. Менеджера/тимлидов-людей не трогаем.
        Participation.objects.all().delete()
        Application.objects.all().delete()
        EventFeedback.objects.all().delete()
        EventCategoryMap.objects.all().delete()
        EventStaff.objects.all().delete()
        EventShift.objects.all().delete()
        Event.objects.all().delete()
        OrganizerOrganizationLink.objects.all().delete()
        Organizer.objects.all().delete()
        Organization.objects.all().delete()
        DobroRuSharedLink.objects.all().delete()
        Season.objects.all().delete()
        # Демо-волонтёры прошлых прогонов (telegram_id с префиксом demo_vol:).
        User.objects.filter(telegram_id__startswith='demo_vol:').delete()

    # ---------------------------------------------------------------- consts
    def _ensure_consts(self):
        defaults = [
            ('churn_no_show_penalty', 1), ('churn_activity_base', 3),
            ('churn_blacklist_threshold', 5), ('churn_carryover_min_seasons', 2),
            ('pgas_hours_threshold', 100), ('recruit_close_ratio', 1.2), ('late_cancel_hours', 24),
        ]
        for name, value in defaults:
            Const.objects.get_or_create(name=name, defaults={'value': value})

    # --------------------------------------------------------------- seasons
    def _create_seasons(self):
        """4 непересекающихся сезона учебного года 2025/26: осень→зима→весна→лето.
        «Текущий» — лето 2026 (сегодня 08.07.2026 по контексту), прошлые — осень/зима/весна."""
        specs = [
            ('autumn', 'Осень 2025', date(2025, 9, 1), date(2025, 11, 30)),
            ('winter', 'Зима 2025/26', date(2025, 12, 1), date(2026, 2, 28)),
            ('spring', 'Весна 2026', date(2026, 3, 1), date(2026, 5, 31)),
            ('summer', 'Лето 2026', date(2026, 6, 1), date(2026, 8, 31)),
        ]
        result = []
        for key, label, start, end in specs:
            season = Season.objects.create(start_season=start, end_season=end)
            result.append({'key': key, 'label': label, 'obj': season, 'start': start, 'end': end})
            self.stdout.write(f'  сезон {label}: {start} — {end}')
        return result

    # ------------------------------------------------------- refs: loc / cat
    def _ensure_locations(self):
        names = ['Покровский бульвар', 'Мясницкая, 20', 'Городской парк', 'Набережная', 'Стадион «Юность»', 'Онлайн', 'Библиотека им. Пушкина']
        return [Location.objects.get_or_create(name=n)[0] for n in names]

    def _ensure_categories(self):
        return {n: EventCategory.objects.get_or_create(name=n)[0] for n in CATEGORIES}

    # ------------------------------------------------------------ orgs
    def _create_orgs(self):
        orgs, organizers = [], []
        for i, (name, desc) in enumerate(ORG_NAMES):
            org = Organization.objects.create(name=name, description=desc, internal_rating=RND.randint(6, 10))
            fn = RND.choice(FIRST_NAMES_F + FIRST_NAMES_M)
            ln = RND.choice(LAST_NAMES)
            organizer = Organizer.objects.create(
                first_name=fn, last_name=ln,
                phone=f'+7 9{RND.randint(10, 99)} {RND.randint(100, 999)}-{RND.randint(10, 99)}-{RND.randint(10, 99)}',
                email=f'org{i}@example.org', telegram_username=f'org_contact_{i}',
            )
            OrganizerOrganizationLink.objects.create(organizer=organizer, organization=org)
            orgs.append(org)
            organizers.append(organizer)
        return orgs, organizers

    # ------------------------------------------------------------ staff
    def _ensure_staff_users(self):
        manager_user = User.objects.filter(login='123').first()
        if not manager_user:
            manager_user = User.objects.create(
                telegram_id='web:123', login='123', first_name='Admin', last_name='Manager', status='active',
            )
            manager_user.set_password('123')
            manager_user.save()
        if not Manager.objects.filter(user=manager_user, revoked_at__isnull=True).exists():
            Manager.objects.create(user=manager_user)

        team_leads = []
        for i in range(4):
            tl = User.objects.get_or_create(
                telegram_id=f'demo_vol:tl_{i}',
                defaults={
                    'telegram_username': f'teamlead_{i}',
                    'first_name': RND.choice(FIRST_NAMES_M), 'last_name': RND.choice(LAST_NAMES),
                    'status': 'active',
                },
            )[0]
            if not TeamLeader.objects.filter(user=tl, revoked_at__isnull=True).exists():
                TeamLeader.objects.create(user=tl)
            team_leads.append(tl)
        return manager_user, team_leads

    # -------------------------------------------------------- volunteers
    def _create_volunteers(self, seasons):
        """~80 волонтёров с разными профилями: активные, новички (created_at в текущем сезоне),
        на грани blacklist, в blacklist. Часть будет «вернувшимися» (участия в двух сезонах)."""
        volunteers = []
        threshold = 5.0
        for i in range(80):
            gender = RND.random() < 0.5
            fn = RND.choice(FIRST_NAMES_M if gender else FIRST_NAMES_F)
            ln = RND.choice(LAST_NAMES) + ('' if gender else 'а')
            # created_at: часть зарегистрирована давно, часть — новички этим (летним) сезоном.
            if i < 15:
                created = self._dt(seasons[3]['start'] + timedelta(days=RND.randint(0, 25)))  # новички лета
            else:
                created = self._dt(seasons[0]['start'] - timedelta(days=RND.randint(1, 200)))  # старички

            status = 'active'
            churn = 0.0
            if i < 3:
                status, churn = 'blacklisted', RND.uniform(threshold, threshold + 4)  # в блэклисте
            elif i < 8:
                churn = RND.uniform(threshold * 0.8, threshold - 0.1)  # на грани

            user = User.objects.create(
                telegram_id=f'demo_vol:{i}',
                telegram_username=f'vol_{i}',
                first_name=fn, last_name=ln,
                gender='male' if gender else 'female',
                status=status, churn_score=round(churn, 2),
                phone=f'+7 9{RND.randint(10, 99)} {RND.randint(1000000, 9999999)}',
                created_at=created,
            )
            # created_at auto_now_add — переопределяем напрямую.
            User.objects.filter(pk=user.pk).update(created_at=created)
            volunteers.append(user)
        return volunteers

    # ---------------------------------------------------------- dobro links
    def _create_shared_links(self):
        return [
            DobroRuSharedLink.objects.create(
                title='Общая — конференции, весна 2026', url='https://dobro.ru/project/1001',
                link_type='project', period_hint='весна 2026',
            ),
            DobroRuSharedLink.objects.create(
                title='Общая — экоакции, лето 2026', url='https://dobro.ru/project/1002',
                link_type='project', period_hint='лето 2026',
            ),
        ]

    # ------------------------------------------------------- finished events
    def _create_finished_events(self, count, seasons, locations, categories, orgs, organizers,
                                 manager, team_leads, volunteers, shared_links):
        titles = list(EVENT_TITLES)
        RND.shuffle(titles)
        created = 0
        for idx in range(count):
            season = seasons[idx % len(seasons)]
            title = titles[idx % len(titles)] + ('' if idx < len(titles) else f' #{idx}')
            # Дата меро — внутри сезона.
            span = (season['end'] - season['start']).days
            day0 = season['start'] + timedelta(days=RND.randint(2, max(2, span - 3)))

            # Кейс мероприятия (детерминированно по индексу, чтобы покрыть всё):
            case = idx % 6
            multiday = (idx % 5 == 0)

            org = orgs[idx % len(orgs)]
            organizer = organizers[idx % len(organizers)]
            dobro_kind, shared = self._dobro_for(idx, shared_links)

            published = self._dt(day0 - timedelta(days=RND.randint(10, 30)), hour=RND.randint(9, 20))
            post_published = published + timedelta(hours=RND.randint(1, 48))

            event = Event.objects.create(
                title=title,
                description=f'Демо-мероприятие ({season["label"]}).',
                location=locations[idx % len(locations)],
                organization=org, organizer=organizer,
                event_status='finished',
                published_at=published,
                post_published_at=post_published,
                people_helped=RND.choice([0, 15, 30, 50, 80, 120, 200]),
                chat_link='https://t.me/demo_chat',
                post_in_chanel_link='https://t.me/demo_channel/1',
                dobro_ru_link_kind=dobro_kind,
                dobro_ru_link='https://dobro.ru/event/%d' % (5000 + idx) if dobro_kind == 'unique' else None,
                dobro_ru_shared_link=shared if dobro_kind == 'shared' else None,
                internal_comment='Прошло штатно.' if case != 2 else 'Были жалобы на организацию.',
            )
            Event.objects.filter(pk=event.pk).update(created_at=published)

            # Категории (1–2 на меро).
            cat_names = RND.sample(CATEGORIES, RND.randint(1, 2))
            for cn in cat_names:
                EventCategoryMap.objects.create(event=event, category=categories[cn])

            # Персонал.
            EventStaff.objects.create(event=event, user=manager, staff_role='manager')
            tl = team_leads[idx % len(team_leads)]
            EventStaff.objects.create(event=event, user=tl, staff_role='team_lead')

            # Смены.
            shifts = []
            n_shifts = 2 if multiday else 1
            for s in range(n_shifts):
                needed = RND.choice([5, 8, 10, 12, 15])
                sd = day0 + timedelta(days=s)
                shift = EventShift.objects.create(
                    event=event, day=sd,
                    start_time=self._dt(sd, hour=10), end_time=self._dt(sd, hour=16),
                    volunteer_needed=needed, recruit_status='closed',
                    location=locations[idx % len(locations)],
                )
                shifts.append(shift)

            self._populate_applications_and_participations(
                event, shifts, volunteers, manager, tl, case, day0,
            )
            self._maybe_feedback(event, volunteers, case)
            created += 1
        return created

    def _dobro_for(self, idx, shared_links):
        r = idx % 3
        if r == 0:
            return 'unique', None
        if r == 1:
            return 'shared', shared_links[idx % len(shared_links)]
        return 'none', None

    def _populate_applications_and_participations(self, event, shifts, volunteers, manager, tl, case, day0):
        """Создаёт заявки на меро с учётом кейса и разворачивает одобренные в участия по сменам."""
        needed_total = sum(s.volunteer_needed for s in shifts)

        # Целевая явка по кейсу:
        #  case 3 → недобор (~50–70%); case 4 → высокий % отказов; иначе полный/около-полный набор.
        if case == 3:
            target_attend = int(needed_total * RND.uniform(0.4, 0.7))
        else:
            target_attend = int(needed_total * RND.uniform(0.85, 1.15))
        target_attend = max(1, min(target_attend, len(volunteers)))

        # Пул кандидатов.
        pool = RND.sample(volunteers, min(len(volunteers), target_attend + RND.randint(4, 12)))

        approved_users = pool[:target_attend]
        reserve_users = pool[target_attend:]

        # case 4 — высокий % отказов: много rejected относительно всех заявок.
        extra_rejected = []
        if case == 4:
            extra_rejected = RND.sample(
                [v for v in volunteers if v not in pool],
                min(len(volunteers) - len(pool), target_attend + 6),
            )

        # Одобренные заявки → участия.
        for user in approved_users:
            app = self._make_application(event, user, day0, status='approved', assigned_by=RND.choice([manager, tl]))
            # Разворачиваем в участия по всем сменам (реалистично — тимлид оставил все).
            for shift in shifts:
                # Явка/неявка: небольшая доля absent_unexcused (влияет на churn), редко excused.
                roll = RND.random()
                if roll < 0.08:
                    st, hours = 'absent_unexcused', 0
                elif roll < 0.12:
                    st, hours = 'absent_excused', 0
                else:
                    st, hours = 'attended', RND.choice([3, 4, 5, 6, 8])
                p = Participation.objects.create(
                    user=user, event_day=shift, status=st,
                    full_shift=(st == 'attended' and RND.random() < 0.7),
                    hours=hours,
                )
                Participation.objects.filter(pk=p.pk).update(created_at=self._dt(shift.day))
                # Простановка Добро.ру: часть attended уже отмечена.
                if st == 'attended' and event.dobro_ru_link_kind != 'none' and RND.random() < 0.5:
                    Participation.objects.filter(pk=p.pk).update(
                        dobro_ru_logged=True, dobro_ru_logged_at=self._dt(shift.day), dobro_ru_logged_by=manager,
                    )

        # Резерв (rejected) и дополнительные отказы.
        for user in reserve_users + extra_rejected:
            self._make_application(event, user, day0, status='rejected', assigned_by=RND.choice([manager, tl]))

    def _make_application(self, event, user, day0, status, assigned_by):
        submitted = self._dt(day0 - timedelta(days=RND.randint(1, 25)), hour=RND.randint(8, 23))
        created = submitted + timedelta(minutes=RND.randint(0, 120))
        assigned = created + timedelta(hours=RND.uniform(0.5, 72))
        app = Application.objects.create(
            event=event, user=user,
            first_name=user.first_name, last_name=user.last_name,
            telegram_username=user.telegram_username, phone=user.phone,
            functions=RND.choice(['Регистрация', 'Логистика', 'Навигация', 'Помощь на точке', 'Фото']),
            role_in_event='Волонтёр',
            submitted_at=submitted, status=status, assigned_by=assigned_by, assigned_at=assigned,
        )
        Application.objects.filter(pk=app.pk).update(created_at=created)
        return app

    def _maybe_feedback(self, event, volunteers, case):
        """Отзывы: case 2 → низкий рейтинг (<6), иначе — нормальный."""
        n = RND.randint(2, 6)
        raters = RND.sample(volunteers, min(n, len(volunteers)))
        for u in raters:
            if case == 2:
                rating = RND.randint(2, 5)
            else:
                rating = RND.randint(6, 10)
            EventFeedback.objects.create(event=event, author=u, rating=rating, comment='')

    # ------------------------------------------------ open-recruit / upcoming
    def _create_open_recruiting_events(self, count, locations, categories, orgs, organizers, manager, team_leads, volunteers):
        today = timezone.now().date()
        for i in range(count):
            day0 = today + timedelta(days=RND.randint(7, 21))
            event = Event.objects.create(
                title=f'[Набор открыт] {RND.choice(EVENT_TITLES)}',
                description='Идёт набор волонтёров.',
                location=locations[i % len(locations)],
                organization=orgs[i % len(orgs)], organizer=organizers[i % len(organizers)],
                event_status='upcoming',
                published_at=timezone.now() - timedelta(days=RND.randint(1, 5)),
                post_published_at=timezone.now() - timedelta(days=RND.randint(0, 3)),
                dobro_ru_link_kind='none',
            )
            for cn in RND.sample(CATEGORIES, 1):
                EventCategoryMap.objects.create(event=event, category=categories[cn])
            EventStaff.objects.create(event=event, user=manager, staff_role='manager')
            EventStaff.objects.create(event=event, user=team_leads[i % len(team_leads)], staff_role='team_lead')
            shift = EventShift.objects.create(
                event=event, day=day0,
                start_time=self._dt(day0, hour=11), end_time=self._dt(day0, hour=17),
                volunteer_needed=RND.choice([10, 12, 15]), recruit_status='open',
                location=locations[i % len(locations)],
            )
            # Несколько заявок в статусе new (ожидают обработки) + часть уже approved.
            applicants = RND.sample(volunteers, RND.randint(6, 12))
            for j, user in enumerate(applicants):
                status = 'approved' if j < 3 else 'new'
                assigned_by = manager if status == 'approved' else None
                submitted = timezone.now() - timedelta(hours=RND.randint(1, 96))
                app = Application.objects.create(
                    event=event, user=user, first_name=user.first_name, last_name=user.last_name,
                    telegram_username=user.telegram_username, phone=user.phone,
                    functions='Помощь на точке', role_in_event='Волонтёр',
                    submitted_at=submitted, status=status, assigned_by=assigned_by,
                    assigned_at=(submitted + timedelta(hours=2)) if assigned_by else None,
                )
                if status == 'approved':
                    Participation.objects.create(user=user, event_day=shift, status='attended', hours=0)
            self.stdout.write(f'  меро с открытым набором: {event.title} (id={event.id})')

    def _create_upcoming_event(self, locations, categories, orgs, organizers, manager, team_leads):
        today = timezone.now().date()
        day0 = today + timedelta(days=RND.randint(25, 40))
        event = Event.objects.create(
            title='[Ожидает открытия набора] Большой городской субботник',
            description='Мероприятие создано, набор ещё не открыт.',
            location=locations[0], organization=orgs[0], organizer=organizers[0],
            event_status='upcoming',
            dobro_ru_link_kind='none',
        )
        for cn in RND.sample(CATEGORIES, 1):
            EventCategoryMap.objects.create(event=event, category=categories[cn])
        EventStaff.objects.create(event=event, user=manager, staff_role='manager')
        EventStaff.objects.create(event=event, user=team_leads[0], staff_role='team_lead')
        EventShift.objects.create(
            event=event, day=day0,
            start_time=self._dt(day0, hour=10), end_time=self._dt(day0, hour=15),
            volunteer_needed=20, recruit_status='closed',  # набор ещё не открыт
            location=locations[0],
        )
        self.stdout.write(f'  предстоящее меро (набор не открыт): {event.title} (id={event.id})')

    # ------------------------------------------------------------- stats
    def _recalc_user_stats(self, volunteers):
        """Пересчёт кэш-полей часов/меро (используются в публичных профилях)."""
        from django.db.models import Count, Sum
        for user in volunteers:
            attended = Participation.objects.filter(user=user, status='attended')
            user.total_hours = attended.aggregate(s=Sum('hours'))['s'] or 0
            user.total_event_count = attended.values('event_day__event').distinct().count()
            user.last_event_at = None
            last = attended.order_by('-event_day__day').first()
            if last:
                user.last_event_at = self._dt(last.event_day.day)
            user.save(update_fields=['total_hours', 'total_event_count', 'last_event_at'])

    # ------------------------------------------------------------- helpers
    def _dt(self, d, hour=12, minute=0):
        """date → aware datetime в текущей таймзоне."""
        naive = datetime.combine(d, time(hour, minute))
        return timezone.make_aware(naive)
