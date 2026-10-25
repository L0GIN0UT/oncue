/**
 * Финальный ассет ревила (D13 плана этапа 3a). Ссылка из снимка короткоживущая (ADR 0012; срок
 * задаёт media-pipeline, `GV_MEDIA__LINK_TTL_S`, по умолчанию 60 с; клиент берёт `expires_at`), поэтому
 * файл сразу скачивается в `Blob`. Свежую ссылку даёт `POST .../rounds/{i}/final-clue` — один
 * раз за показ ревила: если скачать не удалось или ссылка истекла по часам клиента (ревил
 * открыт дольше TTL, ассет перекачивается по новой ссылке). Перезапрос снимка не нужен.
 */
import { useEffect, useState } from "react";
import { api, unwrap } from "@/shared/api/client";
import type { ClueOut, FinalClue } from "@/shared/api/types";
import { downloadAsset } from "./assets";

export type FinalClueState = {
  /** `blob:`-адрес скачанного ассета; `null` — ещё качается или не скачался. */
  objectUrl: string | null;
  kind: FinalClue["kind"] | null;
  /** Ассет не получен и после свежей ссылки (или её не выдали: 409/410/429). */
  failed: boolean;
};

export async function issueFinalClue(sessionId: string, roundIndex: number): Promise<ClueOut> {
  return unwrap<ClueOut>(
    await api().game.POST("/v1/sessions/{session_id}/rounds/{index}/final-clue", {
      params: { path: { session_id: sessionId, index: roundIndex } },
    }),
  );
}

/** Сколько мс осталось жить ссылке по часам клиента (≤ 0 — истекла или дата неразборчива). */
export function msUntilExpiry(expiresAt: string, now: number = Date.now()): number {
  const at = Date.parse(expiresAt);
  return Number.isFinite(at) ? at - now : 0;
}

export function useFinalClue(
  sessionId: string,
  roundIndex: number,
  initial: FinalClue | null,
): FinalClueState {
  const [state, setState] = useState<FinalClueState>({
    objectUrl: null,
    kind: initial?.kind ?? null,
    failed: false,
  });
  const url = initial?.url ?? null;
  const expiresAt = initial?.expires_at ?? null;
  const hasClue = url !== null && expiresAt !== null;

  useEffect(() => {
    if (!hasClue || !url || !expiresAt) return;
    const abort = new AbortController();
    let objectUrl: string | null = null;
    let refreshed = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const show = (blob: Blob, kind: FinalClue["kind"]) => {
      if (abort.signal.aborted) return;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
      objectUrl = URL.createObjectURL(blob);
      setState({ objectUrl, kind, failed: false });
    };

    const refresh = async () => {
      if (refreshed || abort.signal.aborted) return;
      refreshed = true;
      if (timer !== null) clearTimeout(timer);
      try {
        const fresh = await issueFinalClue(sessionId, roundIndex);
        show(await downloadAsset(fresh.url, abort.signal), fresh.kind);
      } catch {
        if (abort.signal.aborted) return;
        // Старый Blob (если был) остаётся на экране; без него ревил показывает заглушку.
        setState((s) => (s.objectUrl ? s : { ...s, failed: true }));
      }
    };

    const left = msUntilExpiry(expiresAt);
    if (left <= 0) {
      void refresh();
    } else {
      timer = setTimeout(() => void refresh(), left);
      downloadAsset(url, abort.signal).then(
        (blob) => show(blob, initial?.kind ?? "image"),
        () => void refresh(),
      );
    }

    return () => {
      abort.abort();
      if (timer !== null) clearTimeout(timer);
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
    // Ссылка снимка меняется при каждом ресинке; ревил раунда скачивается один раз за показ.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId, roundIndex, hasClue]);

  return state;
}
