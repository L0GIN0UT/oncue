"use client";
/**
 * `/sanctions` — «Ограничения аккаунта»: вид, срок и причина каждой действующей санкции
 * (social `GET /v1/me/sanctions`). Причина — текст модератора, показывается как есть.
 */
import { useQuery } from "@tanstack/react-query";
import { useFormatter, useTranslations } from "next-intl";
import { useViewer } from "@/features/auth/session";
import { fetchMySanctions, friendsKeys } from "@/features/friends/api";
import { ProfileGate } from "@/features/progression/ProfileView";

export function SanctionsView() {
  const t = useTranslations();
  const f = useFormatter();
  const viewer = useViewer();
  const query = useQuery({
    queryKey: friendsKeys.sanctions,
    queryFn: fetchMySanctions,
    enabled: viewer.kind === "user",
  });

  if (!viewer.pending && viewer.kind !== "user")
    return <ProfileGate variant={viewer.kind === "guest" ? "guest" : "login"} />;

  return (
    <div className="flex flex-col gap-6 pt-6 sm:pt-10">
      <header className="flex flex-col gap-2">
        <h1 className="font-display text-[28px] leading-none font-extrabold">{t("social.sanctions.title")}</h1>
        <p className="max-w-[60ch] text-ink-muted">{t("social.sanctions.lead")}</p>
      </header>
      {query.isPending ? (
        <p className="text-ink-muted" role="status">
          {t("common.loading")}
        </p>
      ) : query.isError ? (
        <p role="alert" className="text-miss">
          {t("social.loadError")}
        </p>
      ) : query.data.items.length === 0 ? (
        <p className="text-ink-muted" data-testid="sanctions-empty">
          {t("social.sanctions.none")}
        </p>
      ) : (
        <ul className="flex flex-col gap-3">
          {query.data.items.map((s) => (
            <li
              key={`${s.kind}-${s.started_at}`}
              data-testid={`sanction-${s.kind}`}
              className="flex flex-col gap-1 rounded-[var(--radius-control)] bg-stage p-4 border-[1.5px] border-rule"
            >
              <h2 className="font-display text-lg font-semibold">
                {t(`social.sanctions.kind.${s.kind}`)}
              </h2>
              <p className="text-sm text-ink-muted">{t(`social.sanctions.kindHint.${s.kind}`)}</p>
              <p className="text-sm" data-testid="sanction-term">
                {s.expires_at
                  ? t("social.sanctions.until", {
                      date: f.dateTime(new Date(s.expires_at), {
                        dateStyle: "long",
                        timeStyle: "short",
                      }),
                    })
                  : t("social.sanctions.forever")}
              </p>
              <p dir="auto" className="text-sm text-ink-muted">
                {t("social.sanctions.reason", { reason: s.reason })}
              </p>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
