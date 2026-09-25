from typing import Optional, List, Dict, Any
import pandas as pd
from data_base.data_controller import *

__all__ = [
    'prossed_application'
]


def prossed_application(application):
    event = get_events(title_filter=application['Название мероприятия'])[0]
    submitted_at = application.get('Дата подачи') if hasattr(application, 'get') else None
    create_application(
        event=event,
        first_name=application['Автор'],
        last_name='',
        telegram_username=application['Телеграм'],
        phone=application['Номер телефона'],
        functions=application['Функция'],
        shifts=application['Смена'],
        role_in_event=application['Роль'],
        user_status=application['Статус обращающегося'],
        submitted_at=submitted_at,
    )