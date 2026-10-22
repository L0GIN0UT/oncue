"use client";
/**
 * Хаб онлайна `/online` (D2 плана этапа 4): создать комнату по онлайн-режиму, войти по коду,
 * открытые лобби и мои последние матчи. Данные — bff `/v1/pages/online`, блоки гаснут
 * независимо; гостю bff отвечает `auth_required` — экран регистрации (D13).
 */
import Link from "next/link";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { Plus } from "lucide-react";
import type { Domain } from "@/shared/api/types";
import { useDynamicT } from "@/shared/i18n/dynamic";
import { createRoom } from "@/features/match/api";
import { matchErrorKey } from "@/features/match/errors";
import { blockData, useOnlinePage, type OnlineModeCard } from "./bff";
import { JoinByCode } from "./JoinByCode";
import { MyMatchList, OpenRoomList } from "./lists";
import { OnlineAuthRequired } from "./OnlineGate";

const HUB_ROOMS = 5;
const HUB_MATCHES = 5;
const DOMAINS: Domain[] = ["music", "film"];

export function OnlineHub() {
  const t = useTranslations();
  const page = useOnlinePage();

  if (page.isPending) {
    return (
      <p className="pt-12 text-ink-muted" role="status">
        {t("common.loading")}
      </p>
    );
  }
  if (page.isError) {
    return (
      <p className="pt-12 text-ink-muted" role="alert">
        {t("online.unavailable")}
      </p>
    );
  }
  // auth_required — гость; session_expired после неудачного refresh — снова войти.
  if (page.data.status !== "ok") return <OnlineAuthRequired />;

  const blocks = page.data.blocks;
  const modes = blockData(blocks?.modes);
  const rooms = blockData(blocks?.open_rooms);
  const matches = blockData(blocks?.my_matches);

  return (
    <div className="flex flex-col gap-12 pt-6 sm:pt-10">
      <header className="flex max-w-3xl flex-col gap-3">
        <h1 className="font-display text-3xl leading-tight font-bold sm:text-4xl">
          {t("online.title")}
        </h1>
        <p className="max-w-[60ch] text-ink-muted">{t("online.lead")}</p>
      </header>

      <div className="grid gap-8 lg:grid-cols-[1fr_minmax(0,26rem)]">
        <section aria-labelledby="online-create" className="flex flex-col gap-4">
          <h2 id="online-create" className="font-display text-xl font-semibold">
            {t("online.create.title")}
          </h2>
          {modes === null ? (
            <p className="text-ink-muted">{t("online.blockUnavailable")}</p>
          ) : (
            <ModePicker modes={modes} />
          )}
        </section>
        <JoinByCode />
      </div>

      <section aria-labelledby="online-lobbies" className="flex flex-col gap-4">
        <div className="flex items-baseline justify-between gap-4">
          <h2 id="online-lobbies" className="font-display text-xl font-semibold">
            {t("online.lobbies.title")}
          </h2>
          <Link
            href="/lobbies"
            className="text-sm text-ink-muted underline-offset-4 hover:text-ink hover:underline"
          >
            {t("online.lobbies.all")}
          </Link>
        </div>
        {rooms === null ? (
          <p className="text-ink-muted">{t("online.blockUnavailable")}</p>
        ) : (
          <OpenRoomList rooms={rooms.items.slice(0, HUB_ROOMS)} />
        )}
      </section>

      <section aria-labelledby="online-matches" className="flex flex-col gap-4">
        <div className="flex items-baseline justify-between gap-4">
          <h2 id="online-matches" className="font-display text-xl font-semibold">
            {t("online.myMatches.title")}
          </h2>
          <Link
            href="/me"
            className="text-sm text-ink-muted underline-offset-4 hover:text-ink hover:underline"
          >
            {t("online.myMatches.all")}
          </Link>
        </div>
        {matches === null ? (
          <p className="text-ink-muted">{t("online.blockUnavailable")}</p>
        ) : (
          <MyMatchList matches={matches.items.slice(0, HUB_MATCHES)} />
        )}
      </section>
    </div>
  );
}

function ModePicker({ modes }: { modes: OnlineModeCard[] }) {
  const t = useTranslations();
  const dt = useDynamicT();
  const router = useRouter();
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const create = async (mode: OnlineModeCard) => {
    setBusy(mode.key);
    setError(null);
    try {
      const room = await createRoom({ mode_key: mode.key });
      router.push(`/room/${room.code}`);
    } catch (e) {
      setError(dt(matchErrorKey(e), t("match.error.generic")));
      setBusy(null);
    }
  };

  if (!modes.length) return <p className="text-ink-muted">{t("online.create.empty")}</p>;
  return (
    <div className="flex flex-col gap-6">
      {DOMAINS.map((domain) => {
        // Рейтинговые комнаты — только из очереди (4b); режимы по сборнику — со страницы сборника.
        const list = modes.filter(
          (m) => m.domain === domain && !m.online.ranked && !m.online.max_collections,
        );
        if (!list.length) return null;
        return (
          <div key={domain} data-domain={domain} className="flex flex-col gap-3">
            <h3 className="text-sm text-[var(--accent)]">{t(`domain.${domain}`)}</h3>
            <ul className="grid gap-3 sm:grid-cols-2">
              {list.map((mode) => (
                <li key={mode.key}>
                  <button
                    type="button"
                    disabled={busy !== null}
                    onClick={() => create(mode)}
                    data-testid={`create-room-${mode.key}`}
                    className="group flex h-full w-full items-start gap-3 rounded-[var(--radius-control)] bg-stage-raised p-4 text-left ring-1 ring-rule ring-inset hover:ring-2 hover:ring-[var(--accent)] focus-visible:ring-2 focus-visible:ring-[var(--accent)] focus-visible:outline-none disabled:opacity-45"
                  >
                    <span className="grid size-9 shrink-0 place-items-center rounded-full bg-[var(--accent)] text-night">
                      <Plus className="size-4" aria-hidden />
                    </span>
                    <span className="flex flex-col gap-1">
                      <span className="font-display font-semibold">
                        {dt(mode.title_key, mode.key)}
                      </span>
                      <span className="text-sm text-ink-muted">
                        {busy === mode.key
                          ? t("online.create.busy")
                          : t("online.create.rules", {
                              kind: t(`online.kind.${mode.online.kind}`),
                              rounds: mode.online.rounds,
                              min: mode.online.players_min,
                              max: mode.online.players_max,
                            })}
                      </span>
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </div>
        );
      })}
      {error && (
        <p role="alert" className="text-miss" data-testid="create-room-error">
          {error}
        </p>
      )}
    </div>
  );
}
