"""Разовая чистка заявок-дублей, накопившихся из-за бага дедупликации при импорте
из Excel (см. `blog/services/import_applications.py`).

Дубль = заявки на ОДНО мероприятие от ОДНОГО человека. Идентичность человека
определяется ровно тем же ключом, что использует импорт при повторной загрузке
(`_application_person_key`: user_id → telegram → телефон → ФИО), поэтому команда
чистки и импорт согласованы — чистка убирает именно то, что импорт впредь считает
дублем и обновляет вместо создания.

Из каждой группы дублей остаётся ОДНА заявка, остальные удаляются.

Кого оставляем в группе (по приоритету):
  1. обработанную заявку (approved/rejected) — у неё могут быть связанные
     Participation/ApplicationShiftDecision, терять их нельзя;
  2. при равенстве — самую позднюю (max id) — в ней самые свежие функция/роль,
     как если бы повторный импорт «обновил» первую запись до этого же содержания.

    python manage.py dedupe_applications --dry-run   # только показать, что удалится
    python manage.py dedupe_applications             # реально удалить
"""
from collections import defaultdict

from django.core.management.base import BaseCommand
from django.db import transaction

from blog.models import Application
from blog.services.import_applications import _application_person_key


def dedup_key(a: Application):
    """Ключ дубля: мероприятие + идентичность человека (та же, что в импорте)."""
    return (a.event_id, _application_person_key(a))


# Приоритет статуса при выборе «кого оставить»: обработанные важнее новых.
_STATUS_PRIORITY = {'approved': 0, 'rejected': 1, 'cancelled': 2, 'new': 3}


def _keep_sort_key(a: Application):
    # Меньше — значит «оставить»: сначала обработанные (у них есть участия/решения),
    # при равном статусе — самая ПОЗДНЯЯ запись (в ней свежайшее содержание, как после
    # «обновления» повторным импортом). -a.id сортирует max id первым.
    return (_STATUS_PRIORITY.get(a.status, 9), -a.id)


class Command(BaseCommand):
    help = 'Удаляет заявки-дубли (полное совпадение полей на одном меро), оставляя одну.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Показать, сколько и каких заявок будет удалено, ничего не удаляя.',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']

        groups = defaultdict(list)
        for a in Application.objects.all():
            groups[dedup_key(a)].append(a)

        to_delete = []
        for key, apps in groups.items():
            if len(apps) < 2:
                continue
            apps_sorted = sorted(apps, key=_keep_sort_key)
            keep = apps_sorted[0]
            duplicates = apps_sorted[1:]
            to_delete.extend(duplicates)

        self.stdout.write(f'Групп с дублями: {sum(1 for v in groups.values() if len(v) > 1)}')
        self.stdout.write(f'Заявок к удалению: {len(to_delete)}')

        if dry_run:
            # Покажем первые несколько удаляемых для наглядности.
            for a in to_delete[:10]:
                self.stdout.write(
                    f'  DEL app#{a.id} event={a.event_id} '
                    f'{a.first_name} {a.last_name or ""} status={a.status}'
                )
            self.stdout.write(self.style.WARNING('DRY-RUN: ничего не удалено.'))
            return

        ids = [a.id for a in to_delete]
        with transaction.atomic():
            deleted, _ = Application.objects.filter(id__in=ids).delete()
        self.stdout.write(self.style.SUCCESS(f'Удалено записей (с каскадами): {deleted}. Заявок-дублей: {len(ids)}.'))
