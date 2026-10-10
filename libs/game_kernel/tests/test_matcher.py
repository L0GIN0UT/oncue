"""Golden-таблица сверки ответов (``tests/golden/matcher.yaml``) и правила допуска."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
import yaml

from gv_game_kernel import AnswerInput, MatchContext, MatchVerdict, MediaItemRef, answer_normalizer
from gv_game_kernel.builtin import ArtistPartial, TitleAliasMatcher, TitleAliasParams
from gv_testkit.game import media_item, person

pytestmark = pytest.mark.unit

GOLDEN: dict[str, Any] = yaml.safe_load(
    (Path(__file__).parent / "golden" / "matcher.yaml").read_text(encoding="utf-8")
)
RULES = {"artist": ArtistPartial()}
MATCHER = TitleAliasMatcher()
NORMALIZER = answer_normalizer()


def _items() -> dict[str, MediaItemRef]:
    items: dict[str, MediaItemRef] = {}
    artist_people: dict[str, list[Any]] = {}
    for key, spec in GOLDEN["items"].items():
        donor = spec.get("artist_ids_from")
        people = artist_people[donor] if donor else [person(n) for n in spec.get("artists", [])]
        artist_people[key] = people
        items[key] = media_item(
            spec["title"],
            domain=spec["domain"],
            aliases=spec["aliases"],
            title_aliases=spec.get("title_aliases"),
            persons=people,
        )
    return items


ITEMS = _items()


def _answer(spec: dict[str, Any]) -> AnswerInput:
    if "text" in spec:
        return AnswerInput(text=str(spec["text"]))
    if spec.get("item_unknown"):
        return AnswerInput(media_item_id=UUID(int=2**100))
    chosen = ITEMS[spec["item"]]
    return AnswerInput(media_item_id=chosen.id, selected=chosen)


@pytest.mark.parametrize("case", GOLDEN["cases"], ids=lambda c: c["id"])
def test_matcher_golden(case: dict[str, Any]) -> None:
    params = TitleAliasParams.model_validate(case.get("params", GOLDEN["params"]))
    target = ITEMS[case["target"]]
    ctx = MatchContext(
        domain=target.domain,
        normalizer=NORMALIZER,
        partial_rules=tuple(RULES[k] for k in case.get("partial", GOLDEN["partial"])),
    )
    result = MATCHER.match(_answer(case["answer"]), target, params, ctx)
    assert result.verdict == MatchVerdict(case["expect"])
    assert result.partial_kind == case.get("partial_kind")


def test_golden_ids_are_unique_and_cover_all_verdicts() -> None:
    ids = [c["id"] for c in GOLDEN["cases"]]
    assert len(ids) == len(set(ids))
    assert {c["expect"] for c in GOLDEN["cases"]} == {"exact", "partial", "miss"}


@pytest.mark.parametrize(
    ("length", "tolerance"), [(1, 0), (4, 0), (5, 1), (9, 1), (10, 2), (120, 2)]
)
def test_tolerance_picks_longest_fitting_rule(length: int, tolerance: int) -> None:
    params = TitleAliasParams.model_validate(GOLDEN["params"])
    assert params.tolerance(length) == tolerance


def test_tolerance_rules_order_does_not_matter() -> None:
    params = TitleAliasParams.model_validate(
        {
            "typo": [{"min_len": 10, "max_distance": 2}, {"min_len": 5, "max_distance": 1}],
            "accept_same_title": False,
        }
    )
    assert (params.tolerance(7), params.tolerance(12)) == (1, 2)


def test_artist_rule_needs_artists_on_target() -> None:
    film = media_item("Film", domain="film", persons=[person("Nolan", "director")])
    ctx = MatchContext(domain="film", normalizer=NORMALIZER)
    assert not ArtistPartial().check(AnswerInput(text="nolan"), film, ctx)
    assert not ArtistPartial().check(AnswerInput(text="?!"), ITEMS["yesterday"], ctx)
    assert not ArtistPartial().check(AnswerInput(media_item_id=film.id), ITEMS["yesterday"], ctx)
