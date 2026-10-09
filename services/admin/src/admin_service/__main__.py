"""``python -m admin_service <команда>``; ``python -m admin_service manage <аргументы>`` —
служебные команды Django (``createsuperuser`` не нужен: вход только через identity)."""

import sys

if len(sys.argv) > 1 and sys.argv[1] == "manage":
    from django.core.management import execute_from_command_line

    from admin_service.main import setup_django

    setup_django()
    execute_from_command_line([sys.argv[0], *sys.argv[2:]])
else:
    from admin_service.main import run

    run()
