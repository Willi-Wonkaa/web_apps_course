"""Разовый пересчёт кэш-статистики пользователей (total_hours/season_hours/*_event_count).

Нужен как разовая починка после бага: `process_application` создавал `participations`
со `status='attended'` при approve заявки, но не пересчитывал агрегаты — они пересчитывались
только при последующей ручной простановке явки тим-лидером. Теперь approve сам вызывает
`recalc_user_aggregates` (см. `blog/services/applications.py`), но у пользователей,
получивших участия до этого исправления, кэш мог остаться на нулях/устаревшим значением.

    python manage.py recalc_stats
"""
from django.core.management.base import BaseCommand

from blog.models import User
from blog.services.stats import recalc_user_aggregates


class Command(BaseCommand):
    help = 'Пересчитывает total_hours/season_hours/*_event_count для всех пользователей.'

    def handle(self, *args, **options):
        users = User.objects.all()
        count = users.count()
        for user in users:
            recalc_user_aggregates(user)
        self.stdout.write(self.style.SUCCESS(f'Пересчитана статистика для {count} пользователей.'))
