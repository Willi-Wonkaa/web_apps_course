"""Блок «Мой опыт» (раздел 3): объединяет системный волонтёрский опыт (участия в
мероприятиях центра) и самозаявленный внешний опыт (ExternalExperience) в единый
хронологический список. Внешний опыт в статистику НЕ входит.
"""
from blog.models import User


def build_experience(user: User) -> dict:
    """Возвращает {'items': [...], 'system_event_count': int, 'total_count': int}.

    items — список записей вида:
      {'kind': 'system'|'external', 'title': str, 'date': date|None,
       'event_id': int|None, 'exp_id': int|None}
    отсортированный по дате (свежие сверху; записи без даты — в конец).

    system_event_count — число РАЗНЫХ мероприятий центра с явкой (для статистики/текста
    экспорта учитываем именно системные, но общий счётчик total_count включает и внешние).
    """
    # Системный опыт: посещённые смены, сгруппированные по мероприятию (одно меро —
    # одна строка, дата = последний посещённый день этого меро).
    participations = (
        user.participations.filter(status='attended')
        .select_related('event_day__event')
    )
    by_event = {}
    for p in participations:
        event = p.event_day.event
        day = p.event_day.day
        cur = by_event.get(event.id)
        if cur is None or day > cur['date']:
            by_event[event.id] = {
                'kind': 'system',
                'title': event.title,
                'date': day,
                'event_id': event.id,
                'exp_id': None,
            }

    items = list(by_event.values())
    system_event_count = len(items)

    for exp in user.external_experiences.all():
        items.append({
            'kind': 'external',
            'title': exp.title,
            'date': exp.date,
            'event_id': None,
            'exp_id': exp.id,
        })

    # Сортировка: с датой — по убыванию даты; без даты — в конце.
    items.sort(key=lambda i: (i['date'] is None, -i['date'].toordinal() if i['date'] else 0))

    return {
        'items': items,
        'system_event_count': system_event_count,
        'total_count': len(items),
    }
