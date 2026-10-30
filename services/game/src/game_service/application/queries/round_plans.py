"""План раундов матча для match (D5 плана этапа 4, ADR 0033).

match при старте матча один раз просит у game план: game разыгрывает раунды тем же
``ModeEngine.draw_rounds``, что и соло, по своим проекциям каталога, ассетов и сборников и
отдаёт ключи ответа, наборы ассетов по уровням и варианты ``pick``. План хранится только в
снимке комнаты match и клиенту не уходит (античит §12 п. 1), поэтому:

* план детерминирован по ``seed`` при том же пуле — повтор старта после handoff даёт тот же
  план;
* в логи и ошибки не попадают ни ``media_item_id``, ни названия, ни алиасы — только ключ
  режима, версия и счётчики.

Состояния у операции нет (чтение проекций), поэтому идемпотентность — сам детерминизм.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from uuid import UUID

import structlog

from game_service.application.artwork import ARTWORK_CLUE_TYPE, ArtworkProfiles
from game_service.application.catalog import ModeCatalog
from game_service.application.ports import GameUnitOfWork, Json, UnitOfWorkFactory
from game_service.application.rules import ModeRules
from game_service.domain.errors import (
    AssetsUnavailableError,
    CollectionDomainMismatchError,
    CollectionNotFoundError,
    CollectionNotSupportedError,
    ModeNotFoundError,
    ModeNotOnlineError,
    OverrideInvalidError,
    TooManyCollectionsError,
)
from game_service.domain.modes import ConfigVersion
from game_service.domain.projection import CollectionProj
from gv_game_kernel import (
    AssetRef,
    CollectionRequiredError,
    MediaItemRef,
    PoolTooSmallError,
    RoundDraw,
    answer_normalizer,
    apply_lobby_overrides,
)

log = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class RoundPlanQuery:
    """Запрос плана (``RoundPlanRequest``; схема и пределы — слой api).

    Attributes:
        mode_key: Онлайн-режим.
        version: Версия конфига; ``None`` — последняя опубликованная.
        seed: Сид розыгрыша — секретный случайный на старт матча (никогда не выводится
            из ``match_id``: иначе ответы вычислимы).
        rounds: Сколько раундов разыграть.
        collection_ids: Сборники лобби (источник ``collection``).
        exclude_ids: Память сыгранного лобби: единицы избегаются по правилу источника.
        clue_type: Переопределение загадки лобби (ADR 0070) или ``None``.
        answer_format: Переопределение формата ответа лобби или ``None``.
    """

    mode_key: str
    version: int | None
    seed: str
    rounds: int
    collection_ids: Sequence[UUID] = ()
    exclude_ids: Sequence[UUID] = ()
    clue_type: str | None = None
    answer_format: str | None = None


def mode_body_json(version: ConfigVersion) -> Json:
    """Тело опубликованной версии (``ModeConfigBody``)."""
    return {
        "key": version.key,
        "version": version.version,
        "domain": version.domain,
        "body": version.body,
    }


class RoundPlans:
    """``PlanRounds`` и ``GetModeVersion`` internal API game.

    Args:
        uow: Фабрика транзакций (только чтение проекций).
        catalog: Каталог режимов.
        candidate_limit: Сколько кандидатов запросить у порта (``GV_GAME__CANDIDATE_LIMIT``).
        artwork: Профили картинки ответа по домену (ADR 0073); ``None`` — картинок в плане нет.
    """

    def __init__(
        self,
        uow: UnitOfWorkFactory,
        catalog: ModeCatalog,
        *,
        candidate_limit: int,
        artwork: ArtworkProfiles | None = None,
    ):
        self._uow = uow
        self._catalog = catalog
        self._candidate_limit = candidate_limit
        self._artwork = artwork or ArtworkProfiles({})

    async def mode_version(self, key: str, version: int) -> Json:
        """Тело опубликованной или архивной версии.

        Raises:
            ModeNotFoundError: такой версии нет.
        """
        found = await self._catalog.source.version(key, version)
        if found is None:
            raise ModeNotFoundError("версия режима не найдена")
        return mode_body_json(found)

    async def plan(self, query: RoundPlanQuery, *, caller: str) -> Json:
        """Разыграть план матча.

        Args:
            query: Что разыграть.
            caller: Клиент сервисного токена (для журнала).

        Raises:
            ModeNotFoundError: нет опубликованной версии (или указанной версии).
            ModeNotOnlineError: режим соло.
            CollectionNotSupportedError | CollectionRequiredError |
            TooManyCollectionsError | CollectionDomainMismatchError: сборники не подходят
                режиму (422).
            CollectionNotFoundError: сборника нет, он скрыт или удалён (404).
            PoolTooSmallError: играбельных единиц меньше ``rounds`` (422,
                ``details = {available, required}``).
            AssetsUnavailableError: набор ассетов единицы пропал между розыгрышем и выдачей.
        """
        if query.version is None:
            version, rules = await self._catalog.latest(query.mode_key)
        else:
            version, rules = await self._catalog.version_of(query.mode_key, query.version)
        if not rules.is_online:
            raise ModeNotOnlineError(
                "план матча — только для онлайн-режима", details={"mode_key": rules.key}
            )
        rules = self._with_overrides(version, rules, query)
        collection_ids = list(dict.fromkeys(query.collection_ids))
        self._check_collection_shape(rules, collection_ids)
        async with self._uow() as uow:
            collections = await self._collections(uow, rules, collection_ids)
            draws = await self._draw(uow, rules, query, collection_ids)
            rounds = await self._rounds(uow, rules, draws, collection_ids)
        log.info(
            "game.round_plan_issued",
            mode_key=rules.key,
            mode_version=rules.version,
            caller=caller,
            rounds=len(rounds),
            collections=len(collections),
            excluded=len(query.exclude_ids),
        )
        return {
            "mode": mode_body_json(version),
            "seed": query.seed,
            "rounds": rounds,
            "collections": [
                {"id": str(c.collection_id), "version": c.content_version} for c in collections
            ],
        }

    # ------------------------------------------------------------------ переопределения
    def _with_overrides(
        self, version: ConfigVersion, rules: ModeRules, query: RoundPlanQuery
    ) -> ModeRules:
        """Правила эффективного конфига лобби (ADR 0070).

        Разрешённость — по ``session.lobby.overridable`` шаблона (ядро), совместимость
        сочетания — тот же валидатор, что у публикации конфига.
        """
        if query.clue_type is None and query.answer_format is None:
            return rules
        body = apply_lobby_overrides(
            version.body, clue_type=query.clue_type, answer_format=query.answer_format
        )
        check = self._catalog.factory.check(body, version=version.version)
        if check.rules is None:
            raise OverrideInvalidError(
                "сочетание переопределений недопустимо для режима",
                details={"mode_key": rules.key, "errors": check.errors},
            )
        return check.rules

    # ------------------------------------------------------------------ сборники
    @staticmethod
    def _check_collection_shape(rules: ModeRules, collection_ids: Sequence[UUID]) -> None:
        uses = rules.engine.uses_collection
        if collection_ids and not uses:
            raise CollectionNotSupportedError("режим не играется по сборнику")
        if uses and not collection_ids:
            raise CollectionRequiredError("режиму нужен сборник")
        limit = rules.max_collections
        if len(collection_ids) > limit:
            raise TooManyCollectionsError(
                "сборников больше, чем допускает режим",
                details={"given": len(collection_ids), "max": limit},
            )

    @staticmethod
    async def _collections(
        uow: GameUnitOfWork, rules: ModeRules, collection_ids: Sequence[UUID]
    ) -> list[CollectionProj]:
        """Сборники из проекции: опубликованы, не скрыты, домена режима (как старт соло)."""
        found: list[CollectionProj] = []
        for collection_id in collection_ids:
            proj = await uow.collections.get(collection_id)
            if proj is None or not proj.is_playable:
                raise CollectionNotFoundError(
                    "сборник не найден", details={"collection_id": str(collection_id)}
                )
            if proj.domain != rules.domain:
                raise CollectionDomainMismatchError(
                    "сборник другого домена", details={"collection_id": str(collection_id)}
                )
            found.append(proj)
        return found

    # ------------------------------------------------------------------ розыгрыш
    async def _draw(
        self,
        uow: GameUnitOfWork,
        rules: ModeRules,
        query: RoundPlanQuery,
        collection_ids: Sequence[UUID],
    ) -> list[RoundDraw]:
        engine = rules.engine
        reveal = rules.reveal_playable_for
        ctx = engine.draw_context(
            seed=query.seed.encode(),
            candidates=uow.candidates_for(reveal),
            collections=uow.collection_port(reveal) if engine.uses_collection else None,
            exclude_ids=frozenset(query.exclude_ids),
            candidate_limit=self._candidate_limit,
            # Источник до D12 читает один ``collection_id``; с ``collection_ids`` ядро
            # чередует сборники (поток A этапа 4a), первый — тот же.
            collection_id=collection_ids[0] if collection_ids else None,
        )
        if collection_ids:
            ctx = replace(ctx, collection_ids=tuple(collection_ids))
        draws = await engine.draw_rounds(ctx, query.rounds)
        if len(draws) < query.rounds:
            raise PoolTooSmallError(
                "в пуле не хватает играбельных единиц",
                details={"available": len(draws), "required": query.rounds},
            )
        return draws

    async def _rounds(
        self,
        uow: GameUnitOfWork,
        rules: ModeRules,
        draws: Sequence[RoundDraw],
        collection_ids: Sequence[UUID],
    ) -> list[Json]:
        targets = [d.target.id for d in draws]
        items = await uow.projection.get_items(targets)
        refs = {i: item.to_ref() for i, item in items.items()}
        assets = await _ready(uow, targets, rules.clue_type, rules.params_hash, collection_ids)
        reveal_for = rules.reveal_playable_for
        reveal = (
            await _ready(uow, targets, reveal_for.clue_type, reveal_for.params_hash, collection_ids)
            if reveal_for is not None
            else {}
        )
        # Картинка ответа (ADR 0073): только каталожный готовый набор, без сборников лобби.
        artwork_hash = self._artwork.params_hash(rules.domain)
        artwork = (
            await _ready(uow, targets, ARTWORK_CLUE_TYPE, artwork_hash, ())
            if artwork_hash is not None
            else {}
        )
        result: list[Json] = []
        for index, draw in enumerate(draws):
            target = draw.target.id
            asset = assets.get(target)
            if asset is None or (reveal_for is not None and target not in reveal):
                # Кандидаты уже отобраны по готовым ассетам: пропасть набор мог только в
                # гонке с выключением. Идентификатор единицы в ошибку не идёт (античит).
                raise AssetsUnavailableError(
                    "набор ассетов раунда недоступен", details={"round": index}
                )
            result.append(
                {
                    "index": index,
                    "answer": _answer_key(refs.get(target, draw.target), rules),
                    "levels": [
                        {"level": level, "asset_set_id": str(asset.asset_set_id)}
                        for level in range(1, rules.levels_count + 1)
                    ],
                    "reveal_asset_set_id": (
                        str(reveal[target].asset_set_id) if target in reveal else None
                    ),
                    "artwork_asset_set_id": (
                        str(artwork[target].asset_set_id) if target in artwork else None
                    ),
                    "options": [
                        {
                            "option_id": o.id,
                            "label_key": o.label_key,
                            "media_item_id": str(o.item.id) if o.item is not None else None,
                            "title": o.item.title if o.item is not None else None,
                        }
                        for o in draw.options
                    ],
                }
            )
        return result


async def _ready(
    uow: GameUnitOfWork,
    ids: Sequence[UUID],
    clue_type: str,
    params_hash: str,
    scopes: Iterable[UUID],
) -> dict[UUID, AssetRef]:
    """Наборы единиц: свой набор любого из сборников лобби первым, иначе каталожный
    (ADR 0029, как ``_round_assets`` соло — там сборник один)."""
    found = await uow.projection.ready_assets(ids, clue_type, params_hash)
    chosen = dict(found)
    for scope in scopes:
        scoped = await uow.projection.ready_assets(ids, clue_type, params_hash, scope=scope)
        for item_id, assets in scoped.items():
            current = chosen.get(item_id)
            if current is None or (assets.scope_id is not None and current.scope_id is None):
                chosen[item_id] = assets
    return {item_id: assets.to_ref() for item_id, assets in chosen.items()}


def _answer_key(item: MediaItemRef, rules: ModeRules) -> Json:
    """Ключ ответа: всё, что нужно ``ModeEngine.check`` в match без вызова game.

    ``partial`` — виды частичного ответа режима (``answer.partial``), если у единицы есть
    персоны: правила частичного ответа сверяют ответ с персонами единицы.
    """
    normalize = answer_normalizer().normalize
    return {
        "media_item_id": str(item.id),
        "domain": item.domain,
        "kind": item.kind,
        "title": item.title,
        "year": item.year,
        "aliases": list(item.aliases),
        "title_aliases": list(item.title_aliases),
        "persons": [
            {
                "person_id": str(p.id),
                "name": p.name,
                "normalized": normalize(p.name),
                "role": p.role,
            }
            for p in item.persons
        ],
        "partial": list(rules.config.answer.partial) if item.persons else [],
    }
