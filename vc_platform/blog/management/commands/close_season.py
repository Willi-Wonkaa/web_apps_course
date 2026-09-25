"""Закрытие текущего сезона (Этап 5.5): снимок churn, carryover, обнуление
season_*. Для запуска вручную или по расписанию (cron/планировщик задач).

    python manage.py close_season           # закрыть текущий сезон
    python manage.py close_season --id 3    # закрыть конкретный сезон
"""
from django.core.management.base import BaseCommand, CommandError

from blog.models import Season
from blog.services.stats import close_season, current_season


class Command(BaseCommand):
    help = 'Закрывает сезон: снимок churn в churn_history, carryover, обнуление сезонных счётчиков.'

    def add_arguments(self, parser):
        parser.add_argument('--id', type=int, help='ID сезона (по умолчанию — текущий)')

    def handle(self, *args, **options):
        if options['id']:
            season = Season.objects.filter(pk=options['id']).first()
            if not season:
                raise CommandError(f'Сезон с id={options["id"]} не найден.')
        else:
            season = current_season()
            if not season:
                raise CommandError('Текущий сезон не найден — задайте сезоны в настройках.')

        next_season = close_season(season)
        self.stdout.write(self.style.SUCCESS(f'Сезон {season} закрыт.'))
        if next_season:
            self.stdout.write(self.style.WARNING(
                f'Создан новый бессрочный сезон с {next_season.start_season} — задайте дату его окончания.'
            ))
