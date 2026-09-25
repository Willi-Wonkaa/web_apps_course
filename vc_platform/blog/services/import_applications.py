"""Импорт заявок из Excel (архитектура.md 4.2, db.md раздел applications).

Ожидаемые колонки (рус.): Мероприятие, Автор, Фамилия, Телеграм, Телефон,
Функция, Смена, Роль, Статус обращающегося, Дата подачи,
Дата начала (опционально), Дата окончания (опционально).

Если мероприятие с таким названием не найдено — строка помечается как
"мероприятие отсутствует" и на превью менеджеру предлагается создать его
(по умолчанию с сегодняшней датой; если указан период дат — на каждый день
периода создаётся отдельная смена EventShift).

Колонка "Смена" (functions/shifts из Excel) — если пустая, считаем, что у
мероприятия одна смена; если задана, пока заглушка: волонтёр допускается на
все смены (выбор конкретной смены по названию из Excel ещё не реализован).
"""
import datetime
import uuid

import openpyxl
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime

from blog.models import Event, EventShift
from blog.services.applications import find_matching_user

COLUMN_MAP = {
    'event': ['Мероприятие', 'Название мероприятия'],
    'first_name': ['Автор', 'Имя'],
    'last_name': ['Фамилия'],
    'telegram_username': ['Телеграм', 'Telegram'],
    'phone': ['Номер телефона', 'Телефон'],
    'functions': ['Функция'],
    'shifts': ['Смена'],
    'role_in_event': ['Роль'],
    'user_status': ['Статус обращающегося'],
    'submitted_at': ['Дата подачи'],
    'event_start_date': ['Дата начала', 'Дата начала мероприятия'],
    'event_end_date': ['Дата окончания', 'Дата окончания мероприятия'],
}


def _find_col(header_row, candidates):
    for idx, cell in enumerate(header_row):
        if cell and str(cell).strip() in candidates:
            return idx
    return None


def _to_date(value):
    if value is None:
        return None
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    if isinstance(value, str):
        return parse_date(value)
    return None


def parse_excel(file) -> dict:
    """Возвращает превью: {'rows': [...], 'errors': [...]}.

    Строки с несуществующим мероприятием не отбрасываются — помечаются
    event_missing=True с предложенными датами (по умолчанию сегодня,
    либо период, если он указан в файле), чтобы менеджер мог создать
    мероприятие прямо при подтверждении импорта.
    """
    wb = openpyxl.load_workbook(file, data_only=True)
    ws = wb.active

    rows_iter = ws.iter_rows(values_only=True)
    header = next(rows_iter, None)
    if not header:
        return {'rows': [], 'errors': ['Файл пуст']}

    col_idx = {key: _find_col(header, names) for key, names in COLUMN_MAP.items()}
    missing = [key for key in ('event', 'first_name') if col_idx[key] is None]
    if missing:
        return {'rows': [], 'errors': [f'Не найдены обязательные колонки: {missing}']}

    today = timezone.now().date()
    rows = []
    errors = []
    for line_no, row in enumerate(rows_iter, start=2):
        if row is None or all(v is None for v in row):
            continue

        def get(key):
            idx = col_idx.get(key)
            return row[idx] if idx is not None and idx < len(row) else None

        event_title_raw = get('event')
        if not event_title_raw:
            errors.append(f'Строка {line_no}: не указано название мероприятия')
            continue
        # .strip() — иначе случайный лишний пробел в ячейке Excel (частый артефакт
        # копипаста) даёт "новое" мероприятие с визуально тем же названием, и дедуп
        # по event.id перестаёт совпадать между загрузками.
        event_title = str(event_title_raw).strip()

        first_name = get('first_name')
        if not first_name:
            errors.append(f'Строка {line_no}: не указано имя заявителя')
            continue

        event = Event.objects.filter(title__iexact=event_title).first()

        event_start = _to_date(get('event_start_date')) or today
        event_end = _to_date(get('event_end_date')) or event_start

        submitted_raw = get('submitted_at')
        submitted_at = None
        if isinstance(submitted_raw, str):
            submitted_at = parse_datetime(submitted_raw)
        elif hasattr(submitted_raw, 'year'):
            submitted_at = submitted_raw

        telegram_username = get('telegram_username')
        phone = get('phone')
        last_name = get('last_name')
        matched_user = find_matching_user(
            telegram_username=telegram_username, phone=phone,
            first_name=first_name, last_name=last_name,
        )

        rows.append({
            'event_id': event.id if event else None,
            'event_title': event_title,
            'event_missing': event is None,
            'event_start_date': event_start.isoformat(),
            'event_end_date': event_end.isoformat(),
            'first_name': first_name,
            'last_name': last_name,
            'telegram_username': telegram_username,
            'phone': phone,
            'functions': get('functions'),
            'shifts': get('shifts'),
            'role_in_event': get('role_in_event'),
            'user_status': get('user_status'),
            'submitted_at': submitted_at,
            'matched_user_id': matched_user.id if matched_user else None,
            'matched_user_label': matched_user.full_name if matched_user else 'Новый профиль',
            'line_no': line_no,
        })

    return {'rows': rows, 'errors': errors}


def _create_event_with_days(row: dict) -> Event:
    """Создаёт мероприятие со сменой (EventShift) на каждый день периода (или одна смена
    на сегодня по умолчанию)."""
    event = Event.objects.create(title=row['event_title'])

    start = row['event_start_date']
    end = row['event_end_date']
    if isinstance(start, str):
        start = datetime.date.fromisoformat(start)
    if isinstance(end, str):
        end = datetime.date.fromisoformat(end)

    day = start
    while day <= end:
        EventShift.objects.create(
            event=event,
            day=day,
            start_time=timezone.make_aware(datetime.datetime.combine(day, datetime.time(0, 0))),
            end_time=timezone.make_aware(datetime.datetime.combine(day, datetime.time(23, 59))),
        )
        day += datetime.timedelta(days=1)

    return event


def _get_or_create_event(row: dict, events_cache: dict) -> Event:
    """Возвращает мероприятие для строки импорта, создавая его не более одного
    раза за импорт: несколько строк с одинаковым новым названием мероприятия
    должны попасть в одно и то же созданное мероприятие, а не плодить дубли."""
    if row.get('event_id'):
        return Event.objects.get(pk=row['event_id'])

    title = row['event_title']
    if title in events_cache:
        return events_cache[title]

    # На случай, если это же мероприятие уже создала более ранняя строка
    # того же импорта (event_id мог быть не проставлен на этапе превью).
    event = Event.objects.filter(title__iexact=title).first()
    if event is None:
        event = _create_event_with_days(row)

    events_cache[title] = event
    return event


def _user_match_key(row: dict):
    """Ключ для сопоставления «новых» заявителей друг с другом в рамках
    одного импорта, чтобы не создавать по пользователю на каждую строку."""
    if row.get('telegram_username'):
        return ('tg', row['telegram_username'].lstrip('@').lower())
    if row.get('phone'):
        return ('phone', row['phone'])
    if row.get('last_name'):
        return ('name', row['first_name'].strip().lower(), row['last_name'].strip().lower())
    return None


def _norm(value):
    return (value or '').strip().lower()


def _norm_username(value):
    """Нормализация telegram_username: без ведущего `@`, в нижнем регистре — иначе
    `@Ivan` и `ivan` считались бы разными людьми."""
    return _norm(value).lstrip('@')


# Поля, по которым отслеживается ИЗМЕНЕНИЕ содержания уже существующей заявки того же
# человека на то же мероприятие (не поле принадлежности — она определяется отдельно
# через person_key, см. _row_person_key/_application_person_key).
_CHANGE_TRACKED_FIELDS = [
    ('functions', 'Функция'),
    ('shifts', 'Смена'),
    ('role_in_event', 'Роль'),
    ('user_status', 'Статус обращающегося'),
]


def _row_person_key(row: dict):
    """Идентичность заявителя внутри строки импорта — та же логика, что и матчинг
    профиля (db.md, правило 5): telegram → phone → ФИО. Используется, чтобы понять,
    подавал ли ЭТОТ ЖЕ человек заявку на ЭТО ЖЕ мероприятие раньше — тогда повторная
    выгрузка Excel обновляет существующую запись, а не плодит дубль."""
    if row.get('matched_user_id'):
        return ('user', row['matched_user_id'])
    telegram_username = row.get('telegram_username')
    if telegram_username:
        return ('tg', _norm_username(telegram_username))
    phone = row.get('phone')
    if phone:
        return ('phone', _norm(phone))
    return ('name', _norm(row.get('first_name')), _norm(row.get('last_name')))


def _application_person_key(application):
    """То же самое для уже существующей в БД заявки — должно давать идентичный
    результат для одного и того же человека, иначе повторный импорт не найдёт совпадение."""
    if application.user_id:
        return ('user', application.user_id)
    if application.telegram_username:
        return ('tg', _norm_username(application.telegram_username))
    if application.phone:
        return ('phone', _norm(application.phone))
    return ('name', _norm(application.first_name), _norm(application.last_name))


def commit_import(rows: list) -> dict:
    """Создаёт мероприятия (если отсутствуют), Application и User из превью-строк.

    Заявка идентифицируется парой (мероприятие, человек) — НЕ полным набором полей.
    Если такой человек уже подавал заявку на это мероприятие (в любом статусе, кроме
    cancelled — отменённую волонтёром заявку повторный импорт не трогает и не
    воскрешает), повторная загрузка Excel ОБНОВЛЯЕТ существующую запись вместо
    создания дубля. Если при этом изменились функция/смена/роль/статус обращающегося —
    в ленту изменений мероприятия (ApplicationChangeLog) добавляется запись, и
    ТЛ/менеджерам уходит уведомление. Если содержимое не изменилось — заявка тихо
    пропускается (как раньше).

    Возвращает {'created': N, 'updated': M, 'skipped': K} — K это заявки без изменений.
    """
    from blog.models import Application, ApplicationChangeLog, User

    events_cache = {}
    new_users_cache = {}
    # Уже существующие заявки на меро, индексированные по person_key — загружаются
    # лениво один раз на меро, дополняются при создании новых заявок в рамках
    # этого же импорта (чтобы повторяющиеся строки одного файла тоже матчились).
    existing_by_event = {}
    created = 0
    updated = 0
    skipped = 0

    for row in rows:
        event = _get_or_create_event(row, events_cache)

        if event.id not in existing_by_event:
            existing_by_event[event.id] = {
                _application_person_key(a): a
                for a in Application.objects.filter(event=event).exclude(status='cancelled')
            }

        person_key = _row_person_key(row)
        existing = existing_by_event[event.id].get(person_key)

        if existing is None:
            # Правило «профиль появляется в момент подачи заявки» (см. раздел 0):
            # у КАЖДОЙ импортной заявки должен быть привязан User — либо найденный
            # матчингом, либо созданный тут же. Раньше создание было опциональным
            # (`create_new_user`), из-за чего заявка могла остаться с user=null и потом
            # блокировать завершение мероприятия (чеклист no_approved_without_user).
            user = None
            if row.get('matched_user_id'):
                user = User.objects.filter(id=row['matched_user_id']).first()
            if user is None:
                match_key = _user_match_key(row)
                user = new_users_cache.get(match_key) if match_key else None
                if user is None:
                    telegram_id = f"import:{uuid.uuid4()}"
                    user = User.objects.create_user(
                        telegram_id=telegram_id,
                        telegram_username=row.get('telegram_username'),
                        first_name=row.get('first_name'),
                        last_name=row.get('last_name'),
                        phone=row.get('phone'),
                    )
                    if match_key:
                        new_users_cache[match_key] = user

            application = Application.objects.create(
                event=event,
                user=user,
                first_name=row['first_name'],
                last_name=row.get('last_name'),
                telegram_username=row.get('telegram_username'),
                phone=row.get('phone'),
                functions=row.get('functions'),
                shifts=row.get('shifts'),
                role_in_event=row.get('role_in_event'),
                user_status=row.get('user_status'),
                submitted_at=row.get('submitted_at') or timezone.now(),
                status='new',
            )
            existing_by_event[event.id][person_key] = application
            name = f'{application.first_name} {application.last_name or ""}'.strip()
            ApplicationChangeLog.objects.create(
                event=event, application=application, kind='new',
                text=f'{name} подал(а) новую заявку.',
            )
            created += 1
            continue

        # Человек уже подавал заявку на это меро — сравниваем отслеживаемые поля,
        # обновляем при расхождении и логируем, что именно изменилось.
        changes = []
        update_fields = []
        for field, label in _CHANGE_TRACKED_FIELDS:
            old_value = getattr(existing, field) or ''
            new_value = row.get(field) or ''
            if _norm(old_value) != _norm(new_value):
                changes.append(f'{label}: «{old_value or "—"}» → «{new_value or "—"}»')
                setattr(existing, field, row.get(field))
                update_fields.append(field)

        if not changes:
            skipped += 1
            continue

        existing.save(update_fields=update_fields)
        name = f'{existing.first_name} {existing.last_name or ""}'.strip()
        change_text = f'{name} изменил(а) заявку — ' + '; '.join(changes)
        ApplicationChangeLog.objects.create(
            event=event, application=existing, kind='changed', text=change_text,
        )
        from blog.services.notifications import notify_application_changed
        notify_application_changed(existing, change_text)
        updated += 1

    return {'created': created, 'updated': updated, 'skipped': skipped}
