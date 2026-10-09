"""Аудио через ffmpeg/ffprobe: подпроцесс без shell, с таймаутом и белыми списками.

Безопасность (§8 п.8): аргументы — список (без shell); вход — только локальный файл
(``-protocol_whitelist file``) и только аудио-демуксеры (``-format_whitelist``): плейлисты
HLS/concat не смогут заставить ffmpeg читать сеть или чужие файлы. Метаданные и главы
вырезаются (``-map_metadata -1 -map_chapters -1``), строка энкодера — тоже (``bitexact``).
"""

from __future__ import annotations

import asyncio
import contextlib
import tempfile
from pathlib import Path

import structlog

from media_pipeline_service.application.errors import GenerationError
from media_pipeline_service.application.ports import NormalizedMedia

log = structlog.get_logger(__name__)

_INPUT_GUARDS = (
    "-protocol_whitelist",
    "file",
    "-format_whitelist",
    "mp3,flac,ogg,wav,mov,mp4,m4a",
)
_STRIP = ("-map_metadata", "-1", "-map_chapters", "-1", "-fflags", "+bitexact")
_FMP4 = ("-movflags", "+frag_keyframe+empty_moov+default_base_moof", "-f", "mp4")
_SAMPLE_RATE = "44100"


class FfmpegTranscoder:
    """Нормализация исходника (FLAC) и нарезка отрывков уровней (AAC в fMP4, loudnorm)."""

    def __init__(
        self,
        *,
        ffmpeg_bin: str = "ffmpeg",
        ffprobe_bin: str = "ffprobe",
        timeout_s: float = 60.0,
        loudnorm: str = "loudnorm=I=-16:TP=-1.5:LRA=11",
        bitrate: str = "128k",
    ) -> None:
        self._ffmpeg = ffmpeg_bin
        self._ffprobe = ffprobe_bin
        self._timeout_s = timeout_s
        self._loudnorm = loudnorm
        self._bitrate = bitrate

    async def _run(self, *args: str) -> bytes:
        process = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            async with asyncio.timeout(self._timeout_s):
                stdout, stderr = await process.communicate()
        except TimeoutError as exc:
            with contextlib.suppress(ProcessLookupError):
                process.kill()
            await process.wait()
            raise GenerationError("transcode.timeout", retryable=True) from exc
        if process.returncode != 0:
            # stderr ffmpeg без пользовательских данных, но длинный — в лог только хвост.
            log.warning(
                "ffmpeg.failed",
                tool=Path(args[0]).name,
                returncode=process.returncode,
                stderr=stderr.decode(errors="replace")[-500:],
            )
            raise GenerationError("source.undecodable", retryable=False)
        return stdout

    async def normalize(self, data: bytes) -> NormalizedMedia:
        """Перекодировать в FLAC (стерео, 44.1 кГц) без метаданных."""
        with tempfile.TemporaryDirectory(prefix="gv-media-") as tmp:
            src, dst = Path(tmp) / "in", Path(tmp) / "out.flac"
            src.write_bytes(data)
            await self._run(
                self._ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error",
                *_INPUT_GUARDS, "-i", str(src),
                "-map", "0:a:0", "-vn", "-sn", "-dn", *_STRIP,
                "-ac", "2", "-ar", _SAMPLE_RATE, "-c:a", "flac", "-f", "flac", str(dst),
            )  # fmt: skip
            return NormalizedMedia(dst.read_bytes(), "audio/flac", "flac")

    async def probe_duration_ms(self, data: bytes) -> int:
        """Длительность по ffprobe."""
        with tempfile.TemporaryDirectory(prefix="gv-media-") as tmp:
            src = Path(tmp) / "in"
            src.write_bytes(data)
            out = await self._run(
                self._ffprobe, "-v", "error", *_INPUT_GUARDS,
                "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(src),
            )  # fmt: skip
        try:
            return round(float(out.decode().strip()) * 1000)
        except ValueError as exc:
            raise GenerationError("source.undecodable", retryable=False) from exc

    async def cut_audio(self, data: bytes, *, start_s: float, duration_s: float) -> bytes:
        """Отрывок ``[start_s, start_s + duration_s)``.

        ``-ss``/``-t`` стоят ДО ``-i``: ограничен вход декодера, поэтому в результат не
        попадает ни один сэмпл после конца отрывка (в отличие от обрезки на выходе).
        """
        with tempfile.TemporaryDirectory(prefix="gv-media-") as tmp:
            src, dst = Path(tmp) / "in", Path(tmp) / "out.m4a"
            src.write_bytes(data)
            await self._run(
                self._ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error",
                *_INPUT_GUARDS, "-ss", f"{start_s:.3f}", "-t", f"{duration_s:.3f}", "-i", str(src),
                "-map", "0:a:0", "-vn", "-sn", "-dn", *_STRIP, "-flags:a", "+bitexact",
                "-af", self._loudnorm, "-ac", "2", "-ar", _SAMPLE_RATE,
                "-c:a", "aac", "-b:a", self._bitrate, *_FMP4, str(dst),
            )  # fmt: skip
            return dst.read_bytes()
