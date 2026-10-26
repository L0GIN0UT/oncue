"use client";
/** Нижний ряд главной (макет Home): Соло, Онлайн, Сборники и Событие — вход в каждый раздел. */
import { useQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { fetchShowcase, collectionKeys } from "@/features/collections/api";
import { useOnlinePage } from "@/features/online/bff";
import { useTierLabel } from "@/features/rating/labels";
import { blockData } from "@/shared/api/blocks";
import { useDynamicT } from "@/shared/i18n/dynamic";
import type { EventBannerCard, ModeCard } from "@/shared/api/types";
import { cn } from "@/shared/lib/cn";
import { Cover } from "@/shared/ui/cover";
import { ListRow } from "@/shared/ui/list-row";
import { useIcons } from "@/shared/ui/theme-provider";
import { Tile } from "@/shared/ui/tile";

const SOLO_PREVIEW = 8;

const CHIP = "rounded-full bg-stage-raised px-[9px] py-[3px] text-xs font-semibold text-ink-muted";

export function SoloTile({ modes }: { modes: ModeCard[] }) {
  const t = useTranslations();
  const dt = useDynamicT();
  const icons = useIcons();
  return (
    <Tile
      href="/solo"
      cap={t("home.solo.cap")}
      aside={
        modes.length > 0 && (
          <span className={CHIP}>{t("home.solo.count", { count: modes.length })}</span>
        )
      }
      title={t("home.solo.title")}
      data-testid="home-solo"
    >
      {modes.length > 0 && (
        <ul className="grid grid-cols-2 gap-1.5">
          {modes.slice(0, SOLO_PREVIEW).map((m) => {
            const Icon = m.domain === "music" ? icons.music : icons.film;
            return (
              <li
                key={m.key}
                className="flex h-[34px] min-w-0 items-center gap-2 rounded-[10px] bg-stage-raised pr-2.5 pl-1.5 text-xs font-medium"
              >
                <Icon aria-hidden className="size-4 flex-none text-music" />
                <span className="truncate">{dt(m.title_key, m.key)}</span>
              </li>
            );
          })}
        </ul>
      )}
      <span className="text-xs text-ink-muted">{t("home.solo.foot")}</span>
    </Tile>
  );
}

/** Строки онлайна нужны только пользователю: гостю хаб отвечает `auth_required`. */
function OnlineRows() {
  const t = useTranslations();
  const tierLabel = useTierLabel();
  const online = useOnlinePage();
  const rooms = blockData(online.data?.blocks?.open_rooms)?.items.length;
  const ratings = blockData(online.data?.blocks?.ratings) ?? [];
  const best = ratings
    .filter((r) => !r.calibrating && r.tier && r.rating != null)
    .sort((a, b) => b.rating! - a.rating!)[0];
  if (rooms == null && !best) return null;
  return (
    <div className="flex flex-col gap-1.5">
      {rooms != null && (
        <ListRow lead={rooms} data-testid="home-online-rooms">
          {t("home.online.rooms", { count: rooms })}
        </ListRow>
      )}
      {best && (
        <ListRow lead={best.rating} accentLead>
          {t("home.online.rating", { tier: tierLabel(best.tier!) })}
        </ListRow>
      )}
    </div>
  );
}

export function OnlineTile({ isUser }: { isUser: boolean }) {
  const t = useTranslations();
  return (
    <Tile
      href="/online"
      cap={t("home.online.cap")}
      title={t("home.online.title")}
      data-testid="home-online"
    >
      {isUser && <OnlineRows />}
      <span className="text-xs text-ink-muted">{t("home.online.foot")}</span>
    </Tile>
  );
}

export function CollectionsTile() {
  const t = useTranslations();
  const icons = useIcons();
  const showcase = useQuery({
    queryKey: collectionKeys.showcase({}),
    queryFn: () => fetchShowcase({}),
    staleTime: 60_000,
  });
  const cards = blockData(showcase.data?.blocks.popular)?.items.slice(0, 4) ?? [];
  return (
    <Tile
      href="/collections"
      cap={t("home.collections.cap")}
      title={t("home.collections.title")}
      data-testid="home-collections"
    >
      {cards.length > 0 && (
        <ul className="grid grid-cols-2 gap-1.5">
          {cards.map((c) => (
            <li key={c.collection_id}>
              <Cover
                label={c.title}
                placeholder={
                  c.domain === "music" ? (
                    <icons.music className="size-6" />
                  ) : (
                    <icons.film className="size-6" />
                  )
                }
              />
            </li>
          ))}
        </ul>
      )}
      <span className="text-xs text-ink-muted">{t("home.collections.foot")}</span>
    </Tile>
  );
}

export function EventTile({ event }: { event: EventBannerCard }) {
  const t = useTranslations();
  const total = event.total ?? 0;
  const found = event.found ?? 0;
  return (
    <Tile
      href={`/events/${event.key}`}
      accent
      cap={t("home.event.cap")}
      aside={
        event.total != null && (
          <span className={CHIP} data-testid="home-event-progress">
            {t("events.progress", { found, total })}
          </span>
        )
      }
      title={<span dir="auto">{event.title}</span>}
      data-testid="home-event"
    >
      {total > 0 && (
        <div aria-hidden className="flex gap-[3px]">
          {Array.from({ length: total }, (_, i) => (
            <span
              key={i}
              className={cn(
                "h-1.5 flex-1 rounded-[3px]",
                i < found ? "bg-music" : "bg-stage-raised",
              )}
            />
          ))}
        </div>
      )}
      <span className="mt-auto w-fit rounded-full bg-music px-3 py-[6px] text-xs font-semibold text-night">
        {t("home.event.open")}
      </span>
    </Tile>
  );
}
