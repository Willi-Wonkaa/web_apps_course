from django.core.management.base import BaseCommand
from django.utils import timezone

from blog.models import (
    Event, EventShift, EventStaff, Organization, Organizer,
    OrganizerOrganizationLink, TeamLeader, User,
)


class Command(BaseCommand):
    help = 'Создаёт демонстрационные профили: тим-лидер Михаил, организатор Анастасия, организация «Беговое сообщество»'

    def handle(self, *args, **options):
        organization, org_created = Organization.objects.get_or_create(
            name='Беговое сообщество',
            defaults={'description': 'Организация, объединяющая любителей бега и беговые мероприятия города.'},
        )

        organizer, organizer_created = Organizer.objects.get_or_create(
            first_name='Анастасия',
            last_name='Волкова',
            defaults={
                'phone': '+7 900 111-22-33',
                'email': 'anastasia@begsoobshestvo.example',
                'telegram_username': 'anastasia_run',
            },
        )
        OrganizerOrganizationLink.objects.get_or_create(organizer=organizer, organization=organization)

        team_leader_user, tl_created = User.objects.get_or_create(
            telegram_id='demo:mikhail_tl',
            defaults={
                'telegram_username': 'mikhail_tl',
                'first_name': 'Михаил',
                'last_name': 'Соколов',
                'status': 'active',
            },
        )
        if not TeamLeader.objects.filter(user=team_leader_user, revoked_at__isnull=True).exists():
            TeamLeader.objects.create(user=team_leader_user)

        event, event_created = Event.objects.get_or_create(
            title='Ночной забег на набережной',
            defaults={
                'description': 'Демонстрационное мероприятие бегового сообщества.',
                'organization': organization,
                'organizer': organizer,
                'event_status': 'upcoming',
                'published_at': timezone.now(),
            },
        )
        if event_created:
            EventShift.objects.create(
                event=event,
                day=timezone.now().date(),
                start_time=timezone.now(),
                end_time=timezone.now() + timezone.timedelta(hours=3),
                volunteer_needed=10,
                recruit_status='open',
            )

        EventStaff.objects.get_or_create(event=event, user=team_leader_user, staff_role='team_lead')

        self.stdout.write(self.style.SUCCESS(
            f'Готово: организация «{organization.name}» (id={organization.id}), '
            f'организатор {organizer.first_name} {organizer.last_name} (id={organizer.id}), '
            f'тим-лидер {team_leader_user.full_name} (id={team_leader_user.id}, telegram_id={team_leader_user.telegram_id}), '
            f'мероприятие «{event.title}» (id={event.id})'
        ))
