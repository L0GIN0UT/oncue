"""Фикстуры тестов catalog. Инфраструктура и фейки общего назначения — из gv_testkit.

Фейки портов самого catalog лежат в ``tests/support`` (testkit не знает доменов сервисов);
каталог добавляется в ``sys.path``, имя модуля уникально (``catalog_fakes``), чтобы не
пересечься с другими сервисами при общем прогоне pytest.
"""

from __future__ import annotations

import sys
from pathlib import Path

_SUPPORT = str(Path(__file__).parent / "support")
if _SUPPORT not in sys.path:
    sys.path.insert(0, _SUPPORT)
