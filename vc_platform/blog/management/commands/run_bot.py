"""Запуск Telegram-бота (long polling) + диспетчера уведомлений (Этап 8).

    python manage.py run_bot
"""
import asyncio
import logging

from django.core.management.base import BaseCommand

from tg_bot.bot import create_bot, create_dispatcher
from tg_bot.notifier import run_notifier_loop

logging.basicConfig(level=logging.INFO)


class Command(BaseCommand):
    help = 'Запускает Telegram-бота (long polling) и диспетчер уведомлений.'

    def handle(self, *args, **options):
        asyncio.run(self._run())

    async def _run(self):
        from aiogram.types import BotCommand

        bot = create_bot()
        dp = create_dispatcher()

        # Нативное меню команд Telegram (кнопка «/» в клиенте, tg-bot.md §9).
        await bot.set_my_commands([
            BotCommand(command='start', description='Начать / главное меню'),
            BotCommand(command='login', description='Авторизоваться на сайте'),
            BotCommand(command='help', description='Помощь'),
        ])

        notifier_task = asyncio.create_task(run_notifier_loop(bot))
        try:
            await dp.start_polling(bot)
        finally:
            notifier_task.cancel()
            await bot.session.close()
