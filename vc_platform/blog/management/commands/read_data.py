from django.core.management.base import BaseCommand
from django.db import connection
from django.apps import apps
import pandas as pd
from data_base.data_controller import *
from data_base.prossed import *




# def create_event(
#     title: str,
#     location: Optional[Location] = None,
#     organization: Optional[Organization] = None,
#     organizer: Optional[Organizer] = None,
#     description: Optional[str] = None,
#     chat_link: Optional[str] = None,
#     post_in_chanel_link: Optional[str] = None,
#     event_status: str = 'upcoming',
#     recruit_status: str = 'open',
#     people_helped: int = 0
# ) -> Event
# def create_event_day(
#     event: Event,
#     day: str,
#     start_time: str,
#     end_time: str,
#     volunteer_needed: int = 0,
#     location: Optional[Location] = None
# ) -> EventDay:
    
class Command(BaseCommand):
    Events = [ 'Ночной забег, 20.06.2026', 'Московский полумарафон, 26.04.2026 - 26.04.2026' ]


    def handle(self, *args, **options):
        # Use read_excel for .xlsx files
        # create_location(name='Lyja')
        location = get_locations(name_filter='Lyja')

        # for event in self.Events:
        #     title, dates = event.split(',')
        #     print(title, '/////', dates)
        #     create_event(title=event,
        #                  location=location[0],
        #                  )
        # print(location)
        df = pd.read_excel('data.xlsx')
        for inx, el in df.iterrows():
            print(el['Автор'])
            prossed_application(el)

        # print(df)
        print(get_full_table('applications'))

            

        # df = pd.read_excel('data.xlsx')
        # print(df.info())

