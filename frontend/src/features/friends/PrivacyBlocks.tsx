"use client";
/** Блокировки (список, разблокировать) и настройки приватности (PUT заменяет целиком). */
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useFormatter, useTranslations } from "next-intl";
import type { PrivacySettings } from "@/shared/api/types";
import { Button } from "@/shared/ui/button";
import { ErrorLine } from "./ErrorLine";
import { useSocialAction } from "./use-action";
import { fetchBlocks, fetchPrivacy, friendsKeys, savePrivacy, unblockPlayer } from "./api";

export function BlocksPanel() {
  const t = useTranslations();
  const f = useFormatter();
  const client = useQueryClient();
  const action = useSocialAction();
  const query = useQuery({ queryKey: friendsKeys.blocks, queryFn: fetchBlocks });

  if (query.isPending)
    return (
      <p className="text-ink-muted" role="status">
        {t("common.loading")}
      </p>
    );
  if (query.isError)
    return (
      <p role="alert" className="text-miss">
        {t("social.loadError")}
      </p>
    );

  const unblock = async (userId: string) => {
    const r = await action.run(`unblock:${userId}`, () => unblockPlayer(userId));
    if (r.ok) await client.invalidateQueries({ queryKey: friendsKeys.blocks });
  };

  return (
    <div className="flex flex-col gap-3">
      <p className="max-w-[60ch] text-sm text-ink-muted">{t("social.blocks.lead")}</p>
      {query.data.length === 0 ? (
        <p className="text-ink-muted" data-testid="blocks-empty">
          {t("social.blocks.empty")}
        </p>
      ) : (
        <ul className="flex flex-col gap-2" aria-label={t("social.blocks.list")}>
          {query.data.map((b) => (
            <li
              key={b.user_id}
              data-testid={`block-${b.nickname}`}
              className="flex flex-wrap items-center gap-3 rounded-[var(--radius-control)] bg-stage px-4 py-3 ring-1 ring-rule"
            >
              <span dir="auto" className="min-w-32 flex-1 font-medium">
                {b.nickname}
              </span>
              <time dateTime={b.created_at} className="text-xs text-ink-faint">
                {f.dateTime(new Date(b.created_at), { dateStyle: "medium" })}
              </time>
              <Button
                size="sm"
                variant="secondary"
                disabled={action.busy}
                onClick={() => void unblock(b.user_id)}
                aria-label={t("social.blocks.unblockLabel", { name: b.nickname })}
                data-testid="unblock"
              >
                {t("social.blocks.unblock")}
              </Button>
            </li>
          ))}
        </ul>
      )}
      <ErrorLine text={action.error} />
    </div>
  );
}

export function PrivacyPanel() {
  const t = useTranslations();
  const client = useQueryClient();
  const action = useSocialAction();
  const query = useQuery({ queryKey: friendsKeys.privacy, queryFn: fetchPrivacy });

  if (query.isPending)
    return (
      <p className="text-ink-muted" role="status">
        {t("common.loading")}
      </p>
    );
  if (query.isError)
    return (
      <p role="alert" className="text-miss">
        {t("social.loadError")}
      </p>
    );

  const settings = query.data;
  const update = async (patch: Partial<PrivacySettings>) => {
    const next = { ...settings, ...patch };
    const r = await action.run("privacy", () => savePrivacy(next));
    if (r.ok) client.setQueryData(friendsKeys.privacy, r.value);
  };

  return (
    <div className="flex max-w-xl flex-col gap-5">
      <p className="text-sm text-ink-muted">{t("social.privacy.lead")}</p>
      <label className="flex items-start gap-3">
        <input
          type="checkbox"
          className="mt-1 size-5 accent-[var(--color-music)]"
          checked={settings.profile_public}
          disabled={action.busy}
          onChange={(e) => void update({ profile_public: e.target.checked })}
          data-testid="privacy-profile"
        />
        <span className="flex flex-col">
          <span className="font-medium">{t("social.privacy.profile")}</span>
          <span className="text-sm text-ink-muted">{t("social.privacy.profileHint")}</span>
        </span>
      </label>
      <label className="flex items-start gap-3">
        <input
          type="checkbox"
          className="mt-1 size-5 accent-[var(--color-music)]"
          checked={settings.stats_public}
          disabled={action.busy}
          onChange={(e) => void update({ stats_public: e.target.checked })}
          data-testid="privacy-stats"
        />
        <span className="flex flex-col">
          <span className="font-medium">{t("social.privacy.stats")}</span>
          <span className="text-sm text-ink-muted">{t("social.privacy.statsHint")}</span>
        </span>
      </label>
      <fieldset className="flex flex-col gap-2">
        <legend className="font-medium">{t("social.privacy.requests")}</legend>
        {(["all", "none"] as const).map((v) => (
          <label key={v} className="flex items-center gap-3">
            <input
              type="radio"
              name="friend_requests"
              className="size-5 accent-[var(--color-music)]"
              checked={settings.friend_requests === v}
              disabled={action.busy}
              onChange={() => void update({ friend_requests: v })}
              data-testid={`privacy-requests-${v}`}
            />
            {t(`social.privacy.requestsValue.${v}`)}
          </label>
        ))}
      </fieldset>
      <p className="text-sm text-ink-faint">{t("social.privacy.history")}</p>
      <ErrorLine text={action.error} />
    </div>
  );
}
