"""Golden-таблица формата ``pick`` (``tests/golden/matcher_pick.yaml``): ``metric_compare``
и ``preference``; единица без метрики не играбельна (её не разыгрывает источник пары)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from gv_game_kernel import (
    AnswerInput,
    Comparison,
    MatchContext,
    MatchVerdict,
    OptionSpec,
    RoundDraw,
    answer_normalizer,
)
from gv_game_kernel.builtin import (
    HIGHER,
    LOWER,
    MetricCompareMatcher,
    MetricCompareParams,
    PairSource,
    PreferenceMatcher,
    PreferenceParams,
)
from gv_game_kernel.builtin.sources import PAIR_OPTIONS
from gv_testkit.game import FakeCandidatePort, media_item

pytestmark = pytest.mark.unit

GOLDEN: dict[str, Any] = yaml.safe_load(
    (Path(__file__).parent / "golden" / "matcher_pick.yaml").read_text(encoding="utf-8")
)
METRIC: str = GOLDEN["metric"]
COMPARISON = Comparison(metric=METRIC, ties=GOLDEN["ties"])


def _film(value: float | None) -> Any:
    return media_item("Film", domain="film", metrics={} if value is None else {METRIC: value})


def _ctx(draw: RoundDraw, comparison: Comparison | None = COMPARISON) -> MatchContext:
    return MatchContext(
        domain="film", normalizer=answer_normalizer(), draw=draw, comparison=comparison
    )


@pytest.mark.parametrize("case", GOLDEN["metric_compare"], ids=lambda c: c["id"])
def test_metric_compare_golden(case: dict[str, Any]) -> None:
    anchor, target = _film(case["anchor"]), _film(case["target"])
    draw = RoundDraw(target=target, shown=(anchor,), options=PAIR_OPTIONS)
    answer = AnswerInput(option_id=case["pick"])
    matcher, params = MetricCompareMatcher(), MetricCompareParams()
    if case["expect"] == "unplayable":
        with pytest.raises(ValueError, match="не играбельна"):
            matcher.match(answer, target, params, _ctx(draw))
        return
    result = matcher.match(answer, target, params, _ctx(draw))
    assert result.verdict == MatchVerdict(case["expect"])


async def test_items_without_metric_are_not_drawn() -> None:
    """Единица без метрики не попадает в цепочку пары (и в фейке, и в самом источнике)."""
    from gv_game_kernel.builtin import PairParams
    from gv_testkit.game import build_engine

    no_metric = [c for c in GOLDEN["metric_compare"] if c["expect"] == "unplayable"]
    assert no_metric
    items = [_film(v) for v in (1.0, 2.0, 3.0, None, None)]
    port = FakeCandidatePort(items)
    engine = build_engine(_pair_body())
    ctx = engine.draw_context(seed=b"s", candidates=port)
    chain = await engine.draw(ctx, 10)
    assert all(item.metric(METRIC) is not None for item in chain)
    assert len(chain) == 3
    assert port.queries[0][0].has_metric == METRIC
    params = PairParams.model_validate(_pair_body()["source"]["params"])
    assert PairSource().comparison(params) == COMPARISON


def _pair_body() -> dict[str, Any]:
    path = Path(__file__).parent / "modes" / "film.pair.rating.yaml"
    body: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    return body


@pytest.mark.parametrize("case", GOLDEN["preference"], ids=lambda c: c["id"])
def test_preference_golden(case: dict[str, Any]) -> None:
    items = {o: media_item(f"Film {o}", domain="film") for o in case["options"]}
    draw = RoundDraw(
        target=items[case["options"][0]],
        shown=tuple(items.values()),
        options=tuple(OptionSpec(id=o, item=i) for o, i in items.items()),
    )
    result = PreferenceMatcher().match(
        AnswerInput(option_id=case["pick"]), draw.target, PreferenceParams(), _ctx(draw, None)
    )
    assert result.verdict == MatchVerdict(case["expect"])


def test_golden_covers_ties_and_both_verdicts() -> None:
    expects = {c["expect"] for c in GOLDEN["metric_compare"]}
    assert expects == {"exact", "miss", "unplayable"}
    assert any(c["anchor"] == c["target"] for c in GOLDEN["metric_compare"])
    ids = [c["id"] for c in GOLDEN["metric_compare"] + GOLDEN["preference"]]
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize(
    ("answer", "draw", "comparison", "message"),
    [
        (AnswerInput(text="x"), None, COMPARISON, "option_id"),
        (AnswerInput(option_id=HIGHER), None, COMPARISON, "option_id"),
        (AnswerInput(option_id="sideways"), "pair", COMPARISON, "нет в раунде"),
        (AnswerInput(option_id=HIGHER), "pair", None, "правило сравнения"),
        (AnswerInput(option_id=HIGHER), "no_anchor", COMPARISON, "опорную"),
        (AnswerInput(option_id="a"), "odd", COMPARISON, "не знает варианта"),
    ],
)
def test_metric_compare_guards(
    answer: AnswerInput, draw: str | None, comparison: Comparison | None, message: str
) -> None:
    anchor, target = _film(5.0), _film(6.0)
    draws = {
        "pair": RoundDraw(target=target, shown=(anchor,), options=PAIR_OPTIONS),
        "no_anchor": RoundDraw(target=target, options=PAIR_OPTIONS),
        "odd": RoundDraw(target=target, shown=(anchor,), options=(OptionSpec("a"),)),
    }
    ctx = MatchContext(
        domain="film",
        normalizer=answer_normalizer(),
        draw=draws[draw] if draw else None,
        comparison=comparison,
    )
    with pytest.raises(ValueError, match=message):
        MetricCompareMatcher().match(answer, target, MetricCompareParams(), ctx)


def test_metric_compare_tie_without_both_correct_is_miss() -> None:
    anchor, target = _film(5.0), _film(5.0)
    draw = RoundDraw(target=target, shown=(anchor,), options=PAIR_OPTIONS)
    ctx = _ctx(draw, Comparison(metric=METRIC, ties="strict"))
    result = MetricCompareMatcher().match(
        AnswerInput(option_id=LOWER), target, MetricCompareParams(), ctx
    )
    assert result.verdict is MatchVerdict.MISS
