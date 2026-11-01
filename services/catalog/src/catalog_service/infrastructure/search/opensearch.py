"""Поисковый индекс в OpenSearch.

Индекс ``<alias>_v2`` и алиас ``<alias>`` (по умолчанию ``catalog_media``): смена маппинга —
новый индекс ``_vN``, переиндексация (``catalog_service.infrastructure.search.reindex``) и
атомарное переключение алиаса, без простоя. v2 добавил ``people``/``person_ids``/``creator``
(поиск по исполнителю и режиссёру, область подсказок); эти поля совместимо добавляются и в
действующий v1 (``put_mapping``), так что новый образ работает до переиндексации.
Запись идёт с ``version_type=external`` (версия агрегата): повтор и устаревшее событие
отклоняются самим OpenSearch (409) — индексация идемпотентна и учитывает порядок.

Тела запросов и маппинг собираются чистыми функциями (``index_body``, ``document_body``,
``autocomplete_body``, ``search_body``) — их проверяют unit-тесты без OpenSearch.
"""

from __future__ import annotations

from typing import Any

import structlog
from opensearchpy import AsyncOpenSearch
from opensearchpy.exceptions import ConflictError as OsConflictError
from opensearchpy.exceptions import OpenSearchException

from catalog_service.application.dto import SearchDocument, SearchQuery, Suggestion
from catalog_service.domain.model import MediaDomain
from gv_core.errors import DependencyUnavailableError

log = structlog.get_logger(__name__)

INDEX_VERSION = 2
_TEXT_FIELDS = ("title", "aliases")
_SOURCE_FIELDS = ["id", "domain", "title", "year", "subtitle", "creator"]


def concrete_index(alias: str) -> str:
    """Имя физического индекса за алиасом."""
    return f"{alias}_v{INDEX_VERSION}"


def _text_field() -> dict[str, Any]:
    """Поле с edge n-gram для префиксов и морфологией ru/en для поиска."""
    return {
        "type": "text",
        "analyzer": "gv_autocomplete_index",
        "search_analyzer": "gv_autocomplete_search",
        "fields": {
            "ru": {"type": "text", "analyzer": "russian"},
            "en": {"type": "text", "analyzer": "english"},
            "raw": {"type": "keyword", "ignore_above": 300},
        },
    }


def added_properties() -> dict[str, Any]:
    """Поля, появившиеся в v2: безопасно добавляются в v1 без пересоздания."""
    return {
        "people": _text_field(),
        "person_ids": {"type": "keyword"},
        "creator": {"type": "keyword", "index": False},
    }


def index_body() -> dict[str, Any]:
    """Настройки и маппинг индекса."""
    return {
        "settings": {
            "number_of_shards": 1,
            "analysis": {
                "filter": {
                    "gv_edge": {"type": "edge_ngram", "min_gram": 1, "max_gram": 20},
                },
                "analyzer": {
                    "gv_autocomplete_index": {
                        "type": "custom",
                        "tokenizer": "standard",
                        "filter": ["lowercase", "gv_edge"],
                    },
                    "gv_autocomplete_search": {
                        "type": "custom",
                        "tokenizer": "standard",
                        "filter": ["lowercase"],
                    },
                },
            },
        },
        "mappings": {
            "dynamic": "strict",
            "properties": {
                "id": {"type": "keyword"},
                "domain": {"type": "keyword"},
                "kind": {"type": "keyword"},
                "status": {"type": "keyword"},
                "title": _text_field(),
                "aliases": _text_field(),
                "original_title": {"type": "keyword", "index": False},
                "subtitle": {"type": "keyword", "index": False},
                "year": {"type": "integer"},
                "popularity": {"type": "float"},
                **added_properties(),
            },
        },
    }


def document_body(doc: SearchDocument) -> dict[str, Any]:
    """Документ индекса. Нормализованные алиасы уже содержат название и транслит."""
    return {
        "id": str(doc.id),
        "domain": doc.domain.value,
        "kind": doc.kind.value,
        "status": doc.status,
        "title": doc.title,
        "aliases": list(doc.aliases),
        "original_title": doc.original_title,
        "subtitle": doc.subtitle,
        "year": doc.year,
        "popularity": doc.popularity,
        "people": list(doc.people),
        "person_ids": list(doc.person_ids),
        "creator": doc.creator,
    }


def _filters(query: SearchQuery) -> list[dict[str, Any]]:
    # Заблокированные единицы фильтруются всегда — и в автокомплите, и в поиске.
    filters: list[dict[str, Any]] = [{"term": {"status": "active"}}]
    if query.domain is not None:
        filters.append({"term": {"domain": query.domain.value}})
    if query.scope_person is not None:
        filters.append({"term": {"person_ids": str(query.scope_person)}})
    return filters


def _ranked(bool_query: dict[str, Any], limit: int) -> dict[str, Any]:
    return {
        "size": limit,
        "_source": _SOURCE_FIELDS,
        "query": {
            "function_score": {
                "query": {"bool": bool_query},
                "field_value_factor": {
                    "field": "popularity",
                    "modifier": "log1p",
                    "missing": 0,
                },
                "boost_mode": "sum",
            }
        },
    }


def autocomplete_body(query: SearchQuery) -> dict[str, Any]:
    """Префиксный запрос: все слова запроса — префиксы слов алиаса.

    Без области ищет и по именам исполнителей и режиссёров (в том числе «автор название»
    через ``cross_fields``). В области персоны (``scope_person``) — только по названию и
    алиасам: имя автора там ничего не даёт и не должно подсвечивать ответ.
    """
    by_title = {"match": {"aliases": {"query": query.normalized, "operator": "and"}}}
    if query.scope_person is None:
        must: dict[str, Any] = {
            "bool": {
                "should": [
                    by_title,
                    {"match": {"people": {"query": query.normalized, "operator": "and"}}},
                    {
                        "multi_match": {
                            "query": query.normalized,
                            "type": "cross_fields",
                            "fields": ["aliases", "people"],
                            "operator": "and",
                        }
                    },
                ],
                "minimum_should_match": 1,
            }
        }
    else:
        must = by_title
    return _ranked(
        {
            "filter": _filters(query),
            "must": [must],
            "should": [
                {"prefix": {"aliases.raw": {"value": query.normalized, "boost": 3}}},
                {"term": {"aliases.raw": {"value": query.normalized, "boost": 5}}},
            ],
        },
        query.limit,
    )


def search_body(query: SearchQuery) -> dict[str, Any]:
    """Нечёткий поиск по названиям и алиасам с морфологией."""
    return _ranked(
        {
            "filter": _filters(query),
            "must": [
                {
                    "multi_match": {
                        "query": query.normalized,
                        "fields": [f"{f}.{sub}" for f in _TEXT_FIELDS for sub in ("ru", "en")]
                        + list(_TEXT_FIELDS),
                        "fuzziness": "AUTO",
                    }
                }
            ],
        },
        query.limit,
    )


def parse_hits(response: dict[str, Any]) -> list[Suggestion]:
    """Ответ ``_search`` → подсказки."""
    result: list[Suggestion] = []
    for hit in response.get("hits", {}).get("hits", []):
        src = hit["_source"]
        result.append(
            Suggestion.from_json(
                {
                    "id": src["id"],
                    "domain": MediaDomain(src["domain"]).value,
                    "title": src["title"],
                    "year": src.get("year"),
                    "subtitle": src.get("subtitle"),
                    "creator": src.get("creator"),
                }
            )
        )
    return result


class OpenSearchIndex:
    """``SearchIndex`` на OpenSearch."""

    def __init__(self, url: str, alias: str, *, timeout_s: float = 2.0) -> None:
        self._client = AsyncOpenSearch(hosts=[url], timeout=timeout_s)
        self._alias = alias
        self._ready = False

    async def close(self) -> None:
        """Закрыть соединения."""
        await self._client.close()

    async def ensure_index(self) -> None:
        """Создать индекс и алиас, если их нет (идемпотентно)."""
        if self._ready:
            return
        try:
            if not await self._client.indices.exists_alias(name=self._alias):
                index = concrete_index(self._alias)
                if not await self._client.indices.exists(index=index):
                    await self._client.indices.create(index=index, body=index_body())
                await self._client.indices.put_alias(index=index, name=self._alias)
                log.info("catalog.search_index_created", index=index, alias=self._alias)
            else:
                # Действующий индекс старой версии: добавляем только новые поля. Это
                # совместимо (dynamic=strict разрешает расширение маппинга) и идемпотентно.
                try:
                    await self._client.indices.put_mapping(
                        index=self._alias, body={"properties": added_properties()}
                    )
                except OpenSearchException:
                    # Не мешаем записи событий: маппинг донастроит ``catalog migrate``.
                    log.warning("catalog.search_mapping_update_failed", alias=self._alias)
        except OpenSearchException as exc:
            raise DependencyUnavailableError(
                "OpenSearch недоступен", code="catalog.search_unavailable"
            ) from exc
        self._ready = True

    async def index(self, doc: SearchDocument) -> bool:
        """Записать документ с внешней версией; устаревшая версия — ``False``."""
        await self.ensure_index()
        try:
            await self._client.index(
                index=self._alias,
                id=str(doc.id),
                body=document_body(doc),
                version=doc.version,
                version_type="external",
            )
        except OsConflictError:
            return False
        except OpenSearchException as exc:
            raise DependencyUnavailableError(
                "OpenSearch недоступен", code="catalog.search_unavailable"
            ) from exc
        return True

    async def autocomplete(self, query: SearchQuery) -> list[Suggestion]:
        """Подсказки по префиксу."""
        return await self._search(autocomplete_body(query))

    async def search(self, query: SearchQuery) -> list[Suggestion]:
        """Нечёткий поиск."""
        return await self._search(search_body(query))

    async def refresh(self) -> None:
        """Сделать записи видимыми для поиска (только тесты и переиндексация)."""
        await self._client.indices.refresh(index=self._alias)

    async def _search(self, body: dict[str, Any]) -> list[Suggestion]:
        try:
            response = await self._client.search(index=self._alias, body=body)
        except OpenSearchException as exc:
            raise DependencyUnavailableError(
                "OpenSearch недоступен", code="catalog.search_unavailable"
            ) from exc
        return parse_hits(response)
