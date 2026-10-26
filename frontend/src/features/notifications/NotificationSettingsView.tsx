"use client";
/**
 * Настройки уведомлений `/notifications/settings`: каналы (в приложении, по почте) по типам из
 * данных notifications (названия — из конфига, локаль игрока) и согласие на информационную
 * рассылку. Переключатель меняется сразу, при ошибке сервера откатывается. Типы, которые нельзя
 * отключить (`user_mutable = false`), заблокированы.
 */
import { useState } from "react";
import Link from "next/link";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import type { EmailSubscription, NotificationSettings, TypeSetting } from "@/shared/api/types";
import { useViewer } from "@/features/auth/session";
import { ProfileGate } from "@/features/progression/ProfileView";
import {
  emailQueryKey,
  fetchEmailSubscription,
  fetchSettings,
  saveEmailSubscription,
  saveSetting,
  settingsQueryKey,
} from "./api";

type Channel = "in_app" | "email";

export function NotificationSettingsView() {
  const t = useTranslations();
  const viewer = useViewer();
  const client = useQueryClient();
  const user = viewer.kind === "user";
  const [error, setError] = useState(false);
  const [saved, setSaved] = useState(false);

  const settings = useQuery({ queryKey: settingsQueryKey, queryFn: fetchSettings, enabled: user });
  const email = useQuery({
    queryKey: emailQueryKey,
    queryFn: fetchEmailSubscription,
    enabled: user,
  });

  if (!viewer.pending && !user)
    return <ProfileGate variant={viewer.kind === "guest" ? "guest" : "login"} />;

  const toggle = async (row: TypeSetting, channel: Channel, value: boolean) => {
    const next = { in_app: row.in_app, email: row.email, [channel]: value };
    const patch = (r: TypeSetting) => (r.type === row.type ? { ...r, ...next } : r);
    setError(false);
    setSaved(false);
    // Сразу показываем выбор; при ошибке вернём прежнее состояние.
    client.setQueryData<NotificationSettings>(settingsQueryKey, (d) =>
      d ? { types: d.types.map(patch) } : d,
    );
    try {
      const fresh = await saveSetting(row.type, next);
      client.setQueryData<NotificationSettings>(settingsQueryKey, (d) =>
        d ? { types: d.types.map((r) => (r.type === fresh.type ? fresh : r)) } : d,
      );
      setSaved(true);
    } catch {
      client.setQueryData<NotificationSettings>(settingsQueryKey, (d) =>
        d ? { types: d.types.map((r) => (r.type === row.type ? row : r)) } : d,
      );
      setError(true);
    }
  };

  const setConsent = async (subscribed: boolean) => {
    const before = client.getQueryData<EmailSubscription>(emailQueryKey);
    setError(false);
    setSaved(false);
    client.setQueryData<EmailSubscription>(emailQueryKey, (d) =>
      d ? { ...d, subscribed } : { subscribed, changed_at: null },
    );
    try {
      client.setQueryData(emailQueryKey, await saveEmailSubscription(subscribed));
      setSaved(true);
    } catch {
      client.setQueryData(emailQueryKey, before);
      setError(true);
    }
  };

  return (
    <div className="flex flex-col gap-8 pt-6 sm:pt-10">
      <header className="flex flex-col gap-2">
        <h1 className="font-display text-[28px] leading-none font-extrabold">{t("notifications.settings.title")}</h1>
        <p className="max-w-[60ch] text-ink-muted">{t("notifications.settings.lead")}</p>
        <Link href="/notifications" className="text-sm underline underline-offset-4">
          {t("notifications.back")}
        </Link>
      </header>

      <div aria-live="polite" className="min-h-6 text-sm">
        {error && (
          <p role="alert" className="text-miss">
            {t("notifications.settings.saveError")}
          </p>
        )}
        {saved && !error && <p className="text-exact">{t("notifications.settings.saved")}</p>}
      </div>

      <section aria-labelledby="types-title" className="flex flex-col gap-3">
        <h2 id="types-title" className="font-semibold">
          {t("notifications.settings.types")}
        </h2>
        {settings.isPending ? (
          <p className="text-ink-muted" role="status">
            {t("common.loading")}
          </p>
        ) : settings.isError ? (
          <p role="alert" className="text-miss">
            {t("notifications.settings.loadError")}
          </p>
        ) : (
          <ul className="flex flex-col gap-3" data-testid="notification-types">
            {settings.data.types.map((row) => (
              <li
                key={row.type}
                data-testid={`notification-type-${row.type}`}
                className="flex flex-col gap-2 rounded-[var(--radius-control)] bg-stage p-4 border-[1.5px] border-rule"
              >
                <span dir="auto" className="font-medium">
                  {row.title}
                </span>
                <div className="flex flex-wrap gap-x-6 gap-y-2">
                  {(["in_app", "email"] as const).map((channel) => (
                    <label key={channel} className="flex items-center gap-2 text-sm">
                      <input
                        type="checkbox"
                        checked={row[channel]}
                        disabled={!row.user_mutable}
                        onChange={(e) => void toggle(row, channel, e.target.checked)}
                        aria-label={`${row.title} — ${
                          channel === "in_app"
                            ? t("notifications.settings.inApp")
                            : t("notifications.settings.email")
                        }`}
                        className="size-5 accent-[var(--color-music)]"
                      />
                      {channel === "in_app"
                        ? t("notifications.settings.inApp")
                        : t("notifications.settings.email")}
                    </label>
                  ))}
                </div>
                {!row.user_mutable && (
                  <p className="text-xs text-ink-faint">{t("notifications.settings.locked")}</p>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>

      <section aria-labelledby="email-title" className="flex flex-col gap-3">
        <h2 id="email-title" className="font-semibold">
          {t("notifications.settings.emailTitle")}
        </h2>
        <p className="text-sm text-ink-muted">{t("notifications.settings.emailLead")}</p>
        {email.isPending ? (
          <p className="text-ink-muted" role="status">
            {t("common.loading")}
          </p>
        ) : email.isError ? (
          <p role="alert" className="text-miss">
            {t("notifications.settings.loadError")}
          </p>
        ) : (
          <>
            <label className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={email.data.subscribed}
                onChange={(e) => void setConsent(e.target.checked)}
                className="size-5 accent-[var(--color-music)]"
                data-testid="email-consent"
              />
              {t("notifications.settings.emailConsent")}
            </label>
            <p className="text-sm text-ink-muted" data-testid="email-consent-state">
              {email.data.subscribed
                ? t("notifications.settings.emailOn")
                : t("notifications.settings.emailOff")}
            </p>
          </>
        )}
      </section>
    </div>
  );
}
