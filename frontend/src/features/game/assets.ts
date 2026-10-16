/**
 * Скачивание ассета по короткоживущей подписанной ссылке (`/media/a/...`, TTL 60 с, ADR 0012).
 * Файл сразу уходит в `Blob`: повтор воспроизведения и показ не требуют новой ссылки.
 */

/** Ссылка выдана, но файл не скачался (истекла, обрыв): нужна новая выдача. */
export class AssetLoadError extends Error {
  constructor(readonly status: number | null) {
    super(`asset load failed: ${status ?? "network"}`);
    this.name = "AssetLoadError";
  }
}

/** Скачать ассет в `Blob`; ошибка сети или не-2xx — {@link AssetLoadError}. */
export async function downloadAsset(url: string, signal?: AbortSignal): Promise<Blob> {
  let res: Response;
  try {
    res = await fetch(new URL(url, globalThis.location.origin), {
      credentials: "omit",
      cache: "no-store",
      signal,
    });
  } catch (e) {
    if (e instanceof DOMException && e.name === "AbortError") throw e;
    throw new AssetLoadError(null);
  }
  if (!res.ok) throw new AssetLoadError(res.status);
  return res.blob();
}
