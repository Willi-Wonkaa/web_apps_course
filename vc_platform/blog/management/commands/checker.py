from django.core.management.base import BaseCommand
from django.db import connection
from django.apps import apps


class Command(BaseCommand):
    help = 'Вывести данные из ВСЕХ таблиц базы данных'

    def add_arguments(self, parser):
        parser.add_argument(
            '--limit',
            type=int,
            default=10,
            help='Лимит строк на таблицу (по умолчанию 10)',
        )
        parser.add_argument(
            '--all-rows',
            action='store_true',
            help='Вывести все строки без лимита',
        )

    def handle(self, *args, **options):
        limit = None if options['all_rows'] else options['limit']

        backend = connection.vendor

        # Получаем список всех таблиц
        with connection.cursor() as cursor:
            if backend == 'sqlite':
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
            elif backend == 'postgresql':
                cursor.execute("""
                    SELECT table_name 
                    FROM information_schema.tables 
                    WHERE table_schema = 'public'
                    ORDER BY table_name
                """)
            elif backend == 'mysql':
                cursor.execute("SHOW TABLES")
            else:
                self.stdout.write(self.style.ERROR(f'Неподдерживаемая БД: {backend}'))
                return

            tables = [row[0] for row in cursor.fetchall()]

        if not tables:
            self.stdout.write(self.style.WARNING('Таблицы не найдены'))
            return

        self.stdout.write(self.style.SUCCESS(f'\nНайдено таблиц: {len(tables)}\n'))

        # Для каждой таблицы выводим данные
        for table_name in tables:
            self._print_table_data(table_name, limit, backend)

    def _print_table_data(self, table_name, limit, backend):
        """Выводит данные из одной таблицы"""
        self.stdout.write(self.style.SQL_TABLE(f'\n{"=" * 60}'))
        self.stdout.write(self.style.SQL_TABLE(f'ТАБЛИЦА: {table_name}'))
        self.stdout.write(self.style.SQL_TABLE(f'{"=" * 60}'))

        try:
            with connection.cursor() as cursor:
                # Получаем имена колонок
                if backend == 'sqlite':
                    cursor.execute(f"PRAGMA table_info([{table_name}])")
                    columns = [row[1] for row in cursor.fetchall()]
                elif backend == 'postgresql':
                    cursor.execute("""
                        SELECT column_name 
                        FROM information_schema.columns 
                        WHERE table_name = %s 
                        ORDER BY ordinal_position
                    """, [table_name])
                    columns = [row[0] for row in cursor.fetchall()]
                elif backend == 'mysql':
                    cursor.execute(f"DESCRIBE `{table_name}`")
                    columns = [row[0] for row in cursor.fetchall()]

                if not columns:
                    self.stdout.write(self.style.WARNING('  Нет колонок'))
                    return

                # Получаем данные
                query = f"SELECT * FROM [{table_name}]" if backend == 'sqlite' else f"SELECT * FROM \"{table_name}\""
                if limit:
                    if backend == 'postgresql':
                        query += f" LIMIT {limit}"
                    elif backend == 'mysql':
                        query += f" LIMIT {limit}"
                    elif backend == 'sqlite':
                        query += f" LIMIT {limit}"

                cursor.execute(query)
                rows = cursor.fetchall()

                if not rows:
                    self.stdout.write(self.style.WARNING('  Таблица пустая\n'))
                    return

                # Выводим заголовки
                header = ' | '.join(str(col).ljust(20) for col in columns)
                self.stdout.write(f'  {header}')
                self.stdout.write(f'  {"-" * len(header)}')

                # Выводим строки
                for row in rows:
                    row_str = ' | '.join(str(val).ljust(20) if val is not None else 'NULL'.ljust(20) for val in row)
                    self.stdout.write(f'  {row_str}')

                self.stdout.write(self.style.NOTICE(f'\n  Всего строк: {len(rows)}' + (' (лимит)' if limit else '')))

        except Exception as e:
            self.stdout.write(self.style.ERROR(f'  Ошибка чтения таблицы: {str(e)}\n'))