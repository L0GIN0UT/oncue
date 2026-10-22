"""Фикстуры тестов match. Инфраструктура и фейки — из gv_testkit.

Двойники и сборки самого match (до слияния потока A — двойники ``ContestRule``) лежат в
``tests/support``: каталог добавляется в ``sys.path``; имена модулей уникальны
(``match_doubles``, ``match_harness``), чтобы не пересечься с другими сервисами.
"""

from __future__ import annotations

import sys
from pathlib import Path

_SUPPORT = str(Path(__file__).parent / "support")
if _SUPPORT not in sys.path:
    sys.path.insert(0, _SUPPORT)
