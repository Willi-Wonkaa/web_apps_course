"""Единая точка запуска всей системы одной командой (implementation-plan.md, Этап 3):

    python manage.py runall

Поднимает Django dev-сервер (`runserver`) и Telegram-бота (`run_bot`, long polling +
диспетчер уведомлений) вместе, в одном процессе. `runserver` запускается как дочерний
процесс (`subprocess`, тот же интерпретатор/manage.py) — это самый надёжный способ не
конфликтовать с его собственной перезагрузкой автоперезапуска (autoreload держит свой
дочерний процесс и слушает файловую систему). Бот работает в основном потоке через asyncio.
Ctrl+C в терминале останавливает оба процесса.
"""
import asyncio
import subprocess
import sys
import logging

from django.core.management.base import BaseCommand

from tg_bot.bot import create_bot, create_dispatcher
from tg_bot.notifier import run_notifier_loop

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Запускает веб-сервер (runserver) и Telegram-бота вместе, одной командой.'

    def add_arguments(self, parser):
        parser.add_argument(
            'addrport', nargs='?', default=None,
            help='Необязательно: адрес:порт для runserver (например 0.0.0.0:8000).',
        )
        parser.add_argument(
            '--no-web', action='store_true',
            help='Не запускать веб-сервер — только бот (для отладки бота отдельно).',
        )

    def handle(self, *args, **options):
        web_process = None
        if not options['no_web']:
            # --noreload: без этого runserver форкает ещё один дочерний процесс для
            # автоперезапуска при изменении файлов — тогда terminate() ниже не остановит
            # реальный обработчик запросов, а только процесс-обёртку.
            cmd = [sys.executable, 'manage.py', 'runserver', '--noreload']
            if options['addrport']:
                cmd.append(options['addrport'])
            self.stdout.write(self.style.SUCCESS(f'Запускаю веб-сервер: {" ".join(cmd)}'))
            web_process = subprocess.Popen(cmd)

        try:
            self.stdout.write(self.style.SUCCESS('Запускаю Telegram-бота (long polling)…'))
            asyncio.run(self._run_bot())
        except KeyboardInterrupt:
            pass
        finally:
            if web_process:
                self.stdout.write('Останавливаю веб-сервер…')
                web_process.terminate()
                try:
                    web_process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    web_process.kill()

    async def _run_bot(self):
        bot = create_bot()
        dp = create_dispatcher()

        notifier_task = asyncio.create_task(run_notifier_loop(bot))
        try:
            await dp.start_polling(bot)
        finally:
            notifier_task.cancel()
            await bot.session.close()
