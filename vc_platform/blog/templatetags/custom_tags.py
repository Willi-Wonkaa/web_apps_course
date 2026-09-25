from django import template

register = template.Library()

@register.filter
def getattribute(obj, attr):
    return getattr(obj, attr, '')


_CHIP_PALETTE = ['c0', 'c1', 'c2', 'c3', 'c4', 'c5', 'c6', 'c7']


@register.filter
def chip_color(value):
    """Стабильный CSS-класс цвета чипа по значению.

    Повторяет алгоритм из base.html (chipColorClass), чтобы серверные и
    клиентские чипы совпадали по цвету. Ключ — id варианта (как в <option value>).
    """
    h = 0
    for ch in str(value):
        h = (h * 31 + ord(ch)) & 0xFFFFFFFF
    return 'cat-chip--' + _CHIP_PALETTE[h % len(_CHIP_PALETTE)]


_EVENT_STATUS_PILL = {
    'upcoming': 'success',
    'ongoing': 'info',
    'passed': 'warning',
    'finished': 'neutral',
}


@register.inclusion_tag('blog/partials/_event_status_pill.html')
def event_status_pill(event):
    """Статус-пилюля мероприятия по вычисляемому статусу (по датам дней), а не по
    хранимому event_status — см. Event.compute_status."""
    status = event.compute_status()
    return {
        'pill_class': _EVENT_STATUS_PILL.get(status, 'neutral'),
        'label': event.compute_status_display(),
    }


@register.filter
def has_open_shift(shifts):
    """True, если хотя бы одна смена в списке имеет открытый набор — recruit_status
    теперь хранится на смене (EventShift), а не на мероприятии целиком."""
    return any(s.recruit_status == 'open' for s in shifts)


_DOW_RU = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс']


def _fmt_day(d):
    return f'{d.day:02d}.{d.month:02d} ({_DOW_RU[d.weekday()]})'


@register.simple_tag
def event_date_range(first_day, last_day):
    """dd.mm (день недели) — для одного дня; dd.mm (день недели) - dd.mm (день недели)
    для периода. Пусто, если у мероприятия нет ни одной смены."""
    if not first_day:
        return '—'
    if not last_day or first_day == last_day:
        return _fmt_day(first_day)
    return f'{_fmt_day(first_day)} - {_fmt_day(last_day)}'


@register.simple_tag
def star_rating(rating, scale=10):
    """Просто n штук ⭐ — round(rating) эмодзи-звёзд подряд, никаких пустых, половинок,
    градиентов и прочей CSS-магии."""
    try:
        rating = float(rating)
    except (TypeError, ValueError):
        rating = 0
    count = max(0, min(round(rating), scale))
    return '⭐' * count