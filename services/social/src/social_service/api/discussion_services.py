"""Use-cases комментариев и жалоб для слоя API (собираются в composition root ``main.py``)."""

from __future__ import annotations

from dataclasses import dataclass

from social_service.application.comments import CommentService
from social_service.application.reports import ReportService


@dataclass(frozen=True, slots=True)
class DiscussionServices:
    """Use-cases ручек комментариев и жалоб."""

    comments: CommentService
    reports: ReportService
