"use client";
/**
 * `/banned` — «Аккаунт заблокирован»: куда клиент переходит, когда запрос или refresh вернул
 * `auth.account_banned` у уже открытой сессии (ADR 0052). Причина и срок не показываются.
 * Единственное действие — выйти из сессии.
 */
import { useState } from "react";
import { useTranslations } from "next-intl";
import { api } from "@/shared/api/client";
import { Button } from "@/shared/ui/button";

export function BannedView() {
  const t = useTranslations();
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);

  async function signOut() {
    setBusy(true);
    setFailed(false);
    try {
      const { response } = await api().identity.POST("/v1/auth/logout");
      if (!response.ok && response.status !== 401) throw new Error(`logout ${response.status}`);
      // Полная перезагрузка: кэш запросов и состояние сессии не должны пережить выход.
      // eslint-disable-next-line @next/next/no-location-assign-relative-destination -- намеренно
      globalThis.location.assign("/");
    } catch {
      setFailed(true);
      setBusy(false);
    }
  }

  return (
    <div className="flex max-w-xl flex-col items-start gap-4 pt-12" data-testid="banned">
      <h1 className="font-display text-[28px] leading-none font-extrabold">{t("auth.banned.title")}</h1>
      <p className="text-ink-muted">{t("auth.banned.text")}</p>
      <Button onClick={signOut} disabled={busy} data-testid="banned-logout">
        {busy ? t("auth.banned.signingOut") : t("auth.banned.signOut")}
      </Button>
      {failed && (
        <p role="alert" className="text-miss" data-testid="banned-error">
          {t("auth.banned.failed")}
        </p>
      )}
    </div>
  );
}
