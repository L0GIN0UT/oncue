"""Тесты JSONL-помощника: разделители юникода не режут строки (проблема 4)."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from gv_cli.jsonl import dumps_line, split_lines
from gv_cli.seed import _jsonl_keys
from gv_cli.seed_real import read_jsonl, write_jsonl

BREAKS = "\u2028\u2029\u0085"
ROW = {"key": "film:tmdb-1", "overview": f"до{BREAKS}после\x0b\x0c\x1c конец"}


class TestSplitLines:
    def test_splits_only_on_newline(self) -> None:
        assert split_lines(f"a{BREAKS}b\nc\r\nd\n") == [f"a{BREAKS}b", "c", "d"]

    def test_empty_text(self) -> None:
        assert split_lines("") == []

    def test_blank_middle_line_is_kept(self) -> None:
        assert split_lines("a\n\nb") == ["a", "", "b"]


class TestDumpsLine:
    def test_breaks_are_escaped(self) -> None:
        line = dumps_line(ROW)
        assert not any(ch in line for ch in "\u2028\u2029\u0085")
        assert json.loads(line) == ROW

    def test_cyrillic_stays_readable(self) -> None:
        assert dumps_line({"t": "Привет"}) == '{"t": "Привет"}'


class TestRoundTrip:
    def test_write_then_read_jsonl(self, tmp_path: Path) -> None:
        path = tmp_path / "films.jsonl"
        assert write_jsonl(path, [ROW, {"key": "film:tmdb-2"}]) == 2
        assert read_jsonl(path) == [ROW, {"key": "film:tmdb-2"}]
        assert path.read_bytes().count(b"\r") == 0  # на Windows запись не добавляет \r

    def test_reads_raw_separator_written_by_others(self, tmp_path: Path) -> None:
        """Файл, записанный ``ensure_ascii=False`` без экранирования, читается целиком."""
        path = tmp_path / "raw.jsonl"
        raw = json.dumps(ROW, ensure_ascii=False) + "\n"
        assert BREAKS[0] in raw
        path.write_text(raw, encoding="utf-8", newline="\n")
        assert read_jsonl(path) == [ROW]
        assert _jsonl_keys(path) == {"film:tmdb-1"}


def _generate_module() -> object:
    spec = importlib.util.spec_from_file_location(
        "gv_media_generate", Path(__file__).parents[3] / "seeds" / "media" / "generate.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    pytest.importorskip("PIL")
    spec.loader.exec_module(module)
    return module


def test_generate_rows_ignore_unicode_separators(tmp_path: Path) -> None:
    generate = _generate_module()
    (tmp_path / "films.jsonl").write_text(
        "# комментарий\n" + json.dumps(ROW, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    assert generate._rows(tmp_path) == [ROW]  # type: ignore[attr-defined]
