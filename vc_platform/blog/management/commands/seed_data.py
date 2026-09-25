from django.core.management.base import BaseCommand
from blog.models import Location, EventCategory, Season, Const
from django.utils import timezone


DEFAULT_CONSTS = [
    ('churn_no_show_penalty', 1, 'Прибавка к чарну за неявку без предупреждения (absent_unexcused)'),
    ('churn_activity_base', 3, 'Числитель бонуса за активность: churn_activity_base / e^(меро за месяц + 1)'),
    ('churn_blacklist_threshold', 5, 'Порог churn_score для перевода в блэклист до следующего сезона'),
    ('churn_carryover_min_seasons', 2, 'Если записей в churn_history >= этого числа — при закрытии сезона снимается только половина чарна'),
    ('pgas_hours_threshold', 100, 'Часов за сезон для получения статуса «Звёздочка»'),
    ('recruit_close_ratio', 1.2, 'Множитель: набор closed, когда approved >= recruit_close_ratio * volunteer_needed'),
    ('late_cancel_hours', 24, 'За сколько часов до меро отказ считается «поздним сливом»'),
]

DEFAULT_LOCATIONS = ['Москва, Покровский бульвар', 'Москва, Мясницкая', 'Онлайн']

DEFAULT_CATEGORIES = ['Спорт', 'Культура', 'Экология', 'Образование', 'Социальная помощь']


class Command(BaseCommand):
    help = 'Заполняет справочники и константы начальными данными'

    def handle(self, *args, **options):
        for name, value, description in DEFAULT_CONSTS:
            Const.objects.update_or_create(
                name=name,
                defaults={'value': value, 'description': description},
            )
        self.stdout.write(self.style.SUCCESS(f'✓ Констант создано/обновлено: {len(DEFAULT_CONSTS)}'))

        for name in DEFAULT_LOCATIONS:
            Location.objects.get_or_create(name=name)
        self.stdout.write(self.style.SUCCESS(f'✓ Локаций создано: {len(DEFAULT_LOCATIONS)}'))

        for name in DEFAULT_CATEGORIES:
            EventCategory.objects.get_or_create(name=name)
        self.stdout.write(self.style.SUCCESS(f'✓ Категорий создано: {len(DEFAULT_CATEGORIES)}'))

        if not Season.objects.exists():
            Season.objects.create(start_season=timezone.now().date(), end_season=None)
            self.stdout.write(self.style.SUCCESS('✓ Стартовый сезон создан (без даты окончания)'))

        self.stdout.write(self.style.SUCCESS('\nВсе справочные данные загружены!'))
