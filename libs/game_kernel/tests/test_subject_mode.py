"""Область ответа по персоне (пункт 49): ``answer.subject_mode`` и ``RoundDraw.subjects``.

В марафоне исполнителя и в сборнике одного исполнителя верно только название, имя исполнителя
ответом не принимается. Настройка - в конфиге режима, ядро не ветвится по ключам плагинов.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from uuid import UUID

import pytest

from gv_game_kernel import AnswerInput, MatchVerdict, ModeEngine
from gv_testkit.game import (
    FakeCandidatePort,
    FakeCollectionPort,
    build_engine,
    media_item,
    person,
)

pytestmark = pytest.mark.unit

SeedBody = Callable[[str], dict[str, Any]]
CID = UUID(int=9_100)


def _engine(seed_body: SeedBody, key: str, mode: str | None) -> ModeEngine:
    body = seed_body(key)
    if mode is None:
        body["answer"].pop("subject_mode", None)
    else:
        body["answer"]["subject_mode"] = mode
    return build_engine(body)


def _verdict(engine: ModeEngine, text: str, item: Any, draw: Any = None) -> MatchVerdict:
    return engine.check(AnswerInput(text=text), item, draw).verdict


# ----------------------------------------------------------------------------- марафон
async def test_marathon_accepts_title_but_not_artist_name(seed_body: SeedBody) -> None:
    artist = person("Ramones Band")
    items = [media_item(f"Track {i}", persons=[artist]) for i in range(8)]
    engine = _engine(seed_body, "music.marathon.artist", "title_only")
    ctx = engine.draw_context(
        seed=b"m", candidates=FakeCandidatePort(items), subject_ids=frozenset({artist.id})
    )
    draws = await engine.draw_rounds(ctx)
    target = draws[0].target
    assert draws[0].subjects == (artist,)
    assert _verdict(engine, target.title, target, draws[0]) is MatchVerdict.EXACT
    assert _verdict(engine, artist.name, target, draws[0]) is MatchVerdict.MISS


async def test_default_mode_still_gives_partial_for_artist(seed_body: SeedBody) -> None:
    """Регрессия: без ``subject_mode`` поведение прежнее (частичный ответ по исполнителю)."""
    artist = person("Ramones Band")
    items = [media_item(f"Track {i}", persons=[artist]) for i in range(8)]
    engine = _engine(seed_body, "music.marathon.artist", None)
    ctx = engine.draw_context(
        seed=b"m", candidates=FakeCandidatePort(items), subject_ids=frozenset({artist.id})
    )
    draws = await engine.draw_rounds(ctx)
    assert draws[0].subjects == ()
    assert _verdict(engine, artist.name, draws[0].target, draws[0]) is MatchVerdict.PARTIAL


# ----------------------------------------------------------------------------- сборник
async def test_single_artist_collection_is_title_only(seed_body: SeedBody) -> None:
    artist = person("Solo Singer")
    items = [media_item(f"Song {i}", persons=[artist]) for i in range(12)]
    engine = _engine(seed_body, "music.collection.intro", "single_subject")
    ctx = engine.draw_context(
        seed=b"c",
        candidates=FakeCandidatePort(),
        collections=FakeCollectionPort({CID: items}),
        collection_id=CID,
    )
    draws = await engine.draw_rounds(ctx, 5)
    assert all(d.subjects == (artist,) for d in draws)
    target = draws[0].target
    assert _verdict(engine, target.title, target, draws[0]) is MatchVerdict.EXACT
    assert _verdict(engine, artist.name, target, draws[0]) is MatchVerdict.MISS


async def test_mixed_collection_keeps_artist_partial(seed_body: SeedBody) -> None:
    a, b = person("Artist A"), person("Artist B")
    items = [media_item(f"A{i}", persons=[a]) for i in range(6)]
    items += [media_item(f"B{i}", persons=[b]) for i in range(6)]
    engine = _engine(seed_body, "music.collection.intro", "single_subject")
    ctx = engine.draw_context(
        seed=b"c",
        candidates=FakeCandidatePort(),
        collections=FakeCollectionPort({CID: items}),
        collection_id=CID,
    )
    draws = await engine.draw_rounds(ctx, 5)
    assert all(d.subjects == () for d in draws)
    target = draws[0].target
    name = target.persons[0].name
    assert _verdict(engine, name, target, draws[0]) is MatchVerdict.PARTIAL


async def test_collection_with_shared_second_artist_has_no_single_subject(
    seed_body: SeedBody,
) -> None:
    """Все треки у двух общих исполнителей: персон две, область не ограничена."""
    a, b = person("Duo A"), person("Duo B")
    items = [media_item(f"D{i}", persons=[a, b]) for i in range(6)]
    engine = _engine(seed_body, "music.collection.intro", "single_subject")
    ctx = engine.draw_context(
        seed=b"c",
        candidates=FakeCandidatePort(),
        collections=FakeCollectionPort({CID: items}),
        collection_id=CID,
    )
    draws = await engine.draw_rounds(ctx, 5)
    assert all(d.subjects == () for d in draws)


async def test_single_subject_ignores_non_scope_roles(seed_body: SeedBody) -> None:
    """Актёр общий для всех фильмов, но не задаёт область (роли artist/director)."""
    actor = person("Actor", role="actor")
    items = [media_item(f"Film {i}", domain="film", persons=[actor]) for i in range(8)]
    engine = _engine(seed_body, "film.collection.frame", "single_subject")
    ctx = engine.draw_context(
        seed=b"c",
        candidates=FakeCandidatePort(),
        collections=FakeCollectionPort({CID: items}),
        collection_id=CID,
    )
    draws = await engine.draw_rounds(ctx, 5)
    assert all(d.subjects == () for d in draws)


def test_seed_templates_declare_subject_mode(seed_body: SeedBody) -> None:
    assert seed_body("music.marathon.artist")["answer"]["subject_mode"] == "title_only"
    assert seed_body("film.marathon.director")["answer"]["subject_mode"] == "title_only"
    for key in ("music.collection.intro", "film.collection.frame"):
        assert seed_body(key)["answer"]["subject_mode"] == "single_subject"


# ----------------------------------------------------------------------------- ревью
def test_single_subject_without_draw_behaves_as_default(seed_body: SeedBody) -> None:
    artist = person("Solo Singer")
    item = media_item("Song", persons=[artist])
    engine = _engine(seed_body, "music.collection.intro", "single_subject")
    assert not engine.title_only_for(None)
    assert _verdict(engine, artist.name, item) is MatchVerdict.PARTIAL


async def test_collection_with_one_foreign_track_keeps_default(seed_body: SeedBody) -> None:
    """Область определяется ВСЕМ сборником: один чужой трек возвращает частичный ответ."""
    a, b = person("Artist A"), person("Artist B")
    items = [media_item(f"A{i}", persons=[a]) for i in range(11)]
    items.append(media_item("Foreign", persons=[b]))
    engine = _engine(seed_body, "music.collection.intro", "single_subject")
    ctx = engine.draw_context(
        seed=b"c",
        candidates=FakeCandidatePort(),
        collections=FakeCollectionPort({CID: items}),
        collection_id=CID,
    )
    assert all(d.subjects == () for d in await engine.draw_rounds(ctx, 5))


async def test_subjects_are_deterministic_for_seed(seed_body: SeedBody) -> None:
    a, b = person("Artist A"), person("Artist B")
    items = [media_item(f"T{i}", persons=[a, b]) for i in range(10)]
    engine = _engine(seed_body, "music.marathon.artist", "title_only")
    ctx = engine.draw_context(
        seed=b"d",
        candidates=FakeCandidatePort(items),
        subject_ids=frozenset({a.id, b.id}),
    )
    first = await engine.draw_rounds(ctx)
    again = await engine.draw_rounds(ctx)
    assert [d.subjects for d in first] == [d.subjects for d in again]
    assert {p.id for p in first[0].subjects} == {a.id, b.id}


def test_single_subject_rejected_in_online_modes(seed_body: SeedBody) -> None:
    from gv_game_kernel import ONLINE_SESSIONS, EngineFactory, parse_mode_config
    from gv_testkit.game import builtin_registry

    body = seed_body("film.duel.frame")
    body["answer"]["subject_mode"] = "single_subject"
    factory = EngineFactory(builtin_registry(), supported_sessions=ONLINE_SESSIONS)
    issues = factory.validate(parse_mode_config(body))
    assert any(i.code == "config.subject_mode_solo_only" for i in issues)
