from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from blog.models import User, Manager, TeamLeader


class Command(BaseCommand):
    help = 'Создаёт пользователя с веб-логином и выдаёт ему роль менеджера'

    def add_arguments(self, parser):
        parser.add_argument('--login', required=True)
        parser.add_argument('--password', required=True)
        parser.add_argument('--first-name', default='')
        parser.add_argument('--last-name', default='')

    @transaction.atomic
    def handle(self, *args, **options):
        login = options['login']
        if User.objects.filter(login=login).exists():
            raise CommandError(f'Пользователь с логином "{login}" уже существует')

        user = User.objects.create_web_manager(
            login=login,
            password=options['password'],
            first_name=options['first_name'],
            last_name=options['last_name'],
        )
        Manager.objects.create(user=user)
        # У менеджера всегда есть профиль тим-лидера — можно назначать его на
        # мероприятия наравне с обычными тим-лидерами (см. blog/services/roles.py:assign_role).
        TeamLeader.objects.create(user=user)

        self.stdout.write(self.style.SUCCESS(
            f'Менеджер создан: login={login}, id={user.id}'
        ))
