"""Проверки zip-пакета: злонамеренные архивы и разбор ``manifest.json`` (без БД и S3)."""

from __future__ import annotations

import io
import json
import stat
import warnings
import zipfile
from pathlib import Path
from typing import Any

import pytest
from media_pipeline_service.application.archive import ArchiveLimits, open_archive
from media_pipeline_service.domain.packages import PackageRejectedError, parse_manifest
from media_pipeline_service.domain.sources import SourceOrigin

pytestmark = pytest.mark.unit

LIMITS = ArchiveLimits(
    max_archive_bytes=1_000_000,
    max_entries=20,
    max_unpacked_bytes=2_000_000,
    max_entry_bytes=500_000,
    max_ratio=100,
    max_manifest_bytes=10_000,
)


def build(
    entries: list[tuple[str | zipfile.ZipInfo, bytes]], *, method: int = zipfile.ZIP_DEFLATED
) -> bytes:
    """Собрать zip в памяти; повторяющиеся имена допустимы (так делает атакующий)."""
    buffer = io.BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with zipfile.ZipFile(buffer, "w", compression=method) as zf:
            for name, data in entries:
                zf.writestr(name, data)
    return buffer.getvalue()


def rejected(tmp_path: Path, raw: bytes, limits: ArchiveLimits = LIMITS) -> str:
    path = tmp_path / "a.zip"
    path.write_bytes(raw)
    with pytest.raises(PackageRejectedError) as exc:
        open_archive(path, limits).close()
    return exc.value.code


def opened(tmp_path: Path, raw: bytes, limits: ArchiveLimits = LIMITS) -> Any:
    path = tmp_path / "ok.zip"
    path.write_bytes(raw)
    return open_archive(path, limits)


def symlink_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name)
    info.create_system = 3
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    return info


# ---------------------------------------------------------------- злонамеренные архивы
@pytest.mark.parametrize(
    "name",
    [
        "../evil.jpg",
        "a/../../evil.jpg",
        "/etc/passwd",
        "C:/windows/evil.jpg",
        "./a.jpg",
        "a//b.jpg",
        "x" * 400,
    ],
)
def test_unsafe_names_are_rejected(tmp_path: Path, name: str) -> None:
    assert rejected(tmp_path, build([(name, b"x")])) == "package.unsafe_path"


@pytest.mark.parametrize(
    ("placeholder", "real"),
    [(b"a_b.jpg", b"a\\b.jpg"), (b"nul_.jpg", b"nul\x00.jpg")],
)
def test_names_zipfile_would_rewrite_are_rejected(
    tmp_path: Path, placeholder: bytes, real: bytes
) -> None:
    """Обратная косая и NUL в имени: Python на Windows их переписывает, патчим байты архива."""
    raw = build([(placeholder.decode(), b"x")]).replace(placeholder, real)
    assert rejected(tmp_path, raw) == "package.unsafe_path"


def test_symlink_entry_is_rejected(tmp_path: Path) -> None:
    raw = build([("manifest.json", b"{}"), (symlink_info("link.jpg"), b"/etc/passwd")])
    assert rejected(tmp_path, raw) == "package.symlink"


def test_special_file_entry_is_rejected(tmp_path: Path) -> None:
    info = zipfile.ZipInfo("dev.jpg")
    info.create_system = 3
    info.external_attr = (stat.S_IFCHR | 0o600) << 16
    assert rejected(tmp_path, build([(info, b"")])) == "package.special_file"


def test_duplicate_names_are_rejected_even_with_other_case(tmp_path: Path) -> None:
    assert rejected(tmp_path, build([("a.jpg", b"1"), ("a.jpg", b"2")])) == "package.duplicate_name"
    assert rejected(tmp_path, build([("A.jpg", b"1"), ("a.JPG", b"2")])) == "package.duplicate_name"


def test_unicode_equivalent_names_are_duplicates(tmp_path: Path) -> None:
    composed, decomposed = "\u0439.jpg", "\u0438\u0306.jpg"
    assert rejected(tmp_path, build([(composed, b"1"), (decomposed, b"2")])) == (
        "package.duplicate_name"
    )


def test_zip_bomb_by_ratio_is_rejected(tmp_path: Path) -> None:
    raw = build([("bomb.bin", b"\x00" * 400_000)])
    assert len(raw) < 5_000  # ~400 КБ нулей сжимаются в доли килобайта
    assert rejected(tmp_path, raw) == "package.ratio_exceeded"


def test_archive_wide_ratio_is_rejected(tmp_path: Path) -> None:
    limits = ArchiveLimits(
        max_archive_bytes=1_000_000,
        max_entries=100,
        max_unpacked_bytes=50_000_000,
        max_entry_bytes=500_000,
        max_ratio=40,
        max_manifest_bytes=10_000,
    )
    # Каждая запись в пределах степени сжатия, но вместе архив «раздувается» сильнее лимита.
    data = bytes(range(256)) * 300
    raw = build([(f"f{i}.bin", data) for i in range(30)])
    assert rejected(tmp_path, raw, limits) == "package.ratio_exceeded"


def test_unpacked_size_limit_is_rejected(tmp_path: Path) -> None:
    limits = ArchiveLimits(
        max_archive_bytes=1_000_000,
        max_entries=100,
        max_unpacked_bytes=250_000,
        max_entry_bytes=200_000,
        max_ratio=10_000,
        max_manifest_bytes=10_000,
    )
    raw = build([(f"f{i}.bin", b"\x01" * 100_000) for i in range(3)])
    assert rejected(tmp_path, raw, limits) == "package.unpacked_too_large"


def test_entry_size_limit_is_rejected(tmp_path: Path) -> None:
    raw = build([("big.bin", bytes(range(256)) * 3000)], method=zipfile.ZIP_STORED)
    assert rejected(tmp_path, raw) == "package.entry_too_large"


def test_entry_count_limit_is_rejected(tmp_path: Path) -> None:
    raw = build([(f"d/{i}.txt", b"x") for i in range(LIMITS.max_entries + 1)])
    assert rejected(tmp_path, raw) == "package.too_many_entries"


def test_archive_size_limit_is_rejected(tmp_path: Path) -> None:
    small = ArchiveLimits(
        max_archive_bytes=100,
        max_entries=20,
        max_unpacked_bytes=2_000_000,
        max_entry_bytes=500_000,
        max_ratio=100,
        max_manifest_bytes=10_000,
    )
    assert rejected(tmp_path, build([("a.bin", bytes(range(256)))]), small) == (
        "package.archive_too_large"
    )


def test_encrypted_entry_is_rejected(tmp_path: Path) -> None:
    raw = bytearray(build([("secret.jpg", b"x")]))
    raw[raw.index(b"PK") + 8] |= 0x1  # флаг шифрования в записи центрального каталога
    assert rejected(tmp_path, bytes(raw)) == "package.encrypted"


def test_exotic_compression_is_rejected(tmp_path: Path) -> None:
    raw = build([("a.bin", b"abc" * 50)], method=zipfile.ZIP_BZIP2)
    assert rejected(tmp_path, raw) == "package.compression_unsupported"


@pytest.mark.parametrize(
    "raw", [b"", b"hello world", b"MZ\x90\x00" + b"\x00" * 60, b"PK\x03\x04junk"]
)
def test_not_a_zip_is_rejected(tmp_path: Path, raw: bytes) -> None:
    assert rejected(tmp_path, raw) == "package.not_zip"


def test_lying_header_does_not_bypass_read_limit(tmp_path: Path) -> None:
    """Чтение файла ограничено лимитом независимо от заявленного размера."""
    archive = opened(tmp_path, build([("a.bin", bytes(range(256)) * 100)]))
    with pytest.raises(PackageRejectedError) as exc:
        archive.read("a.bin", max_bytes=1000)
    assert exc.value.code == "package.entry_too_large"
    archive.close()


def test_total_read_budget_is_enforced(tmp_path: Path) -> None:
    limits = ArchiveLimits(
        max_archive_bytes=1_000_000,
        max_entries=10,
        max_unpacked_bytes=150_000,
        max_entry_bytes=100_000,
        max_ratio=10_000,
        max_manifest_bytes=10_000,
    )
    archive = opened(
        tmp_path, build([("a.bin", b"\x01" * 100_000), ("b.bin", b"\x02" * 40_000)]), limits
    )
    archive.read("a.bin", max_bytes=100_000)
    archive.read("b.bin", max_bytes=100_000)
    with pytest.raises(PackageRejectedError) as exc:
        archive.read("a.bin", max_bytes=100_000)
    assert exc.value.code == "package.unpacked_too_large"
    archive.close()


def test_python_written_zip_is_accepted_and_extension_is_irrelevant(tmp_path: Path) -> None:
    """Имя и расширение не влияют: архив читается по имени записи, на диск ничего не пишется."""
    archive = opened(
        tmp_path, build([("manifest.json", b"{}"), ("dir/", b""), ("dir/x.txt", b"JPEG?")])
    )
    assert archive.names == {"manifest.json", "dir/x.txt"}
    assert archive.read("dir/x.txt", max_bytes=100) == b"JPEG?"
    assert not any(p.name == "x.txt" for p in tmp_path.rglob("*"))
    archive.close()


# ---------------------------------------------------------------- манифест
def manifest(**item: Any) -> bytes:
    base = {
        "key": "film:tmdb-550",
        "kind": "film",
        "files": [{"role": "frame", "path": "a.jpg"}],
    }
    return json.dumps({"version": 1, "items": [{**base, **item}]}).encode()


def test_manifest_empty_license_and_source_are_not_errors() -> None:
    for extra in ({}, {"license_note": ""}, {"license_note": None, "source_url": ""}):
        (item,) = parse_manifest(manifest(**extra), max_files=10).items
        assert item.license_note is None and item.source_url is None and item.origin is None


def test_manifest_full_item() -> None:
    raw = manifest(
        media_item_id="0192a3b4-c5d6-7e8f-9a0b-000000000100",
        origin="tmdb",
        source_url="https://example.org/x",
        license_note="CC0",
        files=[
            {"role": "poster", "path": "p.jpg"},
            {"role": "frame", "path": "f.jpg", "position": 2},
        ],
    )
    (item,) = parse_manifest(raw, max_files=10).items
    assert item.origin is SourceOrigin.TMDB and item.license_note == "CC0"
    assert [(f.role, f.position) for f in item.files] == [("poster", None), ("frame", 2)]


@pytest.mark.parametrize(
    "raw",
    [
        b"not json",
        b"[]",
        b'{"version": 1}',
        b'{"version": 2, "items": []}',
        b'{"version": 1, "items": []}',
        b'{"version": true, "items": [1]}',
        b'{"version": 1, "items": [{"key": "a", "kind": "film"}]}',
    ],
)
def test_manifest_rejects_bad_documents(raw: bytes) -> None:
    with pytest.raises(PackageRejectedError) as exc:
        parse_manifest(raw, max_files=10)
    assert exc.value.code == "package.manifest_invalid"


@pytest.mark.parametrize(
    "item",
    [
        {"key": "Bad Key"},
        {"kind": "series"},
        {"origin": "catalog"},
        {"origin": "nope"},
        {"media_item_id": "not-a-uuid"},
        {"files": []},
        {"files": [{"role": "frame"}]},
        {"files": [{"role": "frame", "path": "a", "position": 0}]},
        {"files": [{"role": "frame", "path": "a", "position": True}]},
        {"files": [{"role": "frame", "path": "a", "extra": 1}]},
        {"license_note": "x" * 501},
        {"extra": 1},
    ],
)
def test_manifest_rejects_bad_items(item: dict[str, Any]) -> None:
    with pytest.raises(PackageRejectedError):
        parse_manifest(manifest(**item), max_files=10)


def test_manifest_unknown_role_and_file_budget() -> None:
    with pytest.raises(PackageRejectedError) as exc:
        parse_manifest(manifest(files=[{"role": "banner", "path": "c.jpg"}]), max_files=10)
    assert exc.value.code == "package.role_unknown"
    many = manifest(files=[{"role": "frame", "path": f"{i}.jpg"} for i in range(11)])
    with pytest.raises(PackageRejectedError) as exc:
        parse_manifest(many, max_files=10)
    assert exc.value.code == "package.too_many_files"


def test_corrupt_deflate_stream_is_a_clean_rejection(tmp_path: Path) -> None:
    """Битый deflate (zlib.error) не должен вылетать необработанным исключением."""
    payload = bytes(range(256)) * 40
    raw = bytearray(build([("manifest.json", payload)]))
    start = raw.index(b"manifest.json") + len(b"manifest.json")
    for i in range(start + 4, start + 24):
        raw[i] ^= 0xFF
    archive = opened(tmp_path, bytes(raw))
    with pytest.raises(PackageRejectedError) as exc:
        archive.read("manifest.json", max_bytes=100_000)
    assert exc.value.code == "package.entry_corrupt"
    archive.close()


@pytest.mark.parametrize(
    "raw",
    [
        b'{"version": 1, "items": [{"key": "a", "kind": [], "files": []}]}',
        b'{"version": 1, "items": [{"key": "a", "kind": "film", '
        b'"files": [{"role": {}, "path": "p"}]}]}',
        b'{"version": 1, "items": ' + b"[" * 50_000 + b"]" * 50_000 + b"}",
    ],
    ids=["kind_is_list", "role_is_object", "deep_nesting"],
)
def test_manifest_with_unhashable_or_deeply_nested_values_is_rejected(raw: bytes) -> None:
    with pytest.raises(PackageRejectedError):
        parse_manifest(raw, max_files=10)
