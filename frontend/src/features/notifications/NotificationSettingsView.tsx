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
import { Switch } from "./Switch";
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
    <div className="flex flex-col gap-6 pt-2">
      <header className="flex flex-col gap-2">
        <h1 className="font-display text-[28px] leading-none font-extrabold">
          {t("notifications.settings.title")}
        </h1>
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
                className="flex flex-col gap-1 rounded-[var(--radius-control)] border-[1.5px] border-rule bg-stage px-4 py-3"
              >
                <span dir="auto" className="font-medium">
                  {row.title}
                </span>
                <div className="grid gap-x-6 sm:grid-cols-2">
                  {(["in_app", "email"] as const).map((channel) => {
                    const channelLabel =
                      channel === "in_app"
                        ? t("notifications.settings.inApp")
                        : t("notifications.settings.email");
                    return (
                      <Switch
                        key={channel}
                        label={channelLabel}
                        checked={row[channel]}
                        disabled={!row.user_mutable}
                        onChange={(e) => void toggle(row, channel, e.target.checked)}
                        aria-label={`${row.title} — ${channelLabel}`}
                      />
                    );
                  })}
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
            <Switch
              label={t("notifications.settings.emailConsent")}
              checked={email.data.subscribed}
              onChange={(e) => void setConsent(e.target.checked)}
              data-testid="email-consent"
              className="rounded-[var(--radius-control)] border-[1.5px] border-rule bg-stage px-4 text-base"
            />
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
