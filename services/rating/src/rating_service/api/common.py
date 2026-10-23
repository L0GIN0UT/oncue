"""Use-cases для роутеров rating (собирает ``main.py``: слой api не знает infrastructure)."""

from __future__ import annotations

from dataclasses import dataclass

from rating_service.application.commands.ladders import LadderCommands, LadderQueries
from rating_service.application.commands.rebuild import RebuildLeaderboard
from rating_service.application.commands.seasons import SeasonCommands
from rating_service.application.queries import RatingQueries


@dataclass(frozen=True, slots=True)
class ApiServices:
    """Use-cases для роутеров."""

    service_name: str
    queries: RatingQueries
    ladder_commands: LadderCommands
    ladder_queries: LadderQueries
    season_commands: SeasonCommands
    rebuild: RebuildLeaderboard
