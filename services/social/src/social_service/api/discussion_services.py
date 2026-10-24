"""Use-cases комментариев, жалоб и санкций для слоя API (собираются в ``main.py``)."""

from __future__ import annotations

from dataclasses import dataclass

from social_service.application.comments import CommentService
from social_service.application.reports import ReportService
from social_service.application.sanctions import SanctionService


@dataclass(frozen=True, slots=True)
class DiscussionServices:
    """Use-cases ручек комментариев, жалоб и санкций."""

    comments: CommentService
    reports: ReportService
    sanctions: SanctionService
