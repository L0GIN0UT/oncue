"use client";
/**
 * Главная `/` — хаб по макету v3 (Home), а не игра: два дейлика и прогресс, лента дейлика, затем
 * входы в Соло, Онлайн, Сборники и событие. Игра дейлика идёт на `/play/<mode_key>`; `/` сессию не
 * стартует. Личные блоки bff приходят только субъекту — как и раньше, сессию восстанавливаем
 * один раз (`useRecoverPersonalBlocks`).
 */
import { useCallback } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import type { HomePage } from "@/shared/api/types";
import { blockData } from "@/shared/api/blocks";
import { useViewer } from "@/features/auth/session";
import { useShellEvent } from "@/features/events/use-event";
import { homeQueryKey, useHomePage, useRecoverPersonalBlocks } from "@/features/daily/DailyCards";
import { cn } from "@/shared/lib/cn";
import { DailyFeed } from "./DailyFeed";
import { DailyTile } from "./DailyTiles";
import { CollectionsTile, EventTile, OnlineTile, SoloTile } from "./HubTiles";
import { ProgressTile } from "./ProgressTile";

export function HomeView({ initial }: { initial: HomePage | null }) {
  const t = useTranslations();
  const client = useQueryClient();
  const viewer = useViewer();
  const { data } = useHomePage(initial);
  // Оболочка (shell) обновляется сразу после находки; блок главной — запасной источник.
  const shellEvent = useShellEvent();
  const blocks = data?.blocks;
  const refresh = useCallback(() => client.invalidateQueries({ queryKey: homeQueryKey }), [client]);
  useRecoverPersonalBlocks([blocks?.dailies.status], refresh);

  if (!blocks)
    return (
      <p className="pt-16 text-center text-ink-muted" role="status">
        {t("home.loading")}
      </p>
    );

  const dailies = blockData(blocks.dailies) ?? [];
  const modes = blockData(blocks.modes) ?? [];
  const event = shellEvent ?? blocks.event ?? null;
  const isUser = viewer.kind === "user";
  return (
    <div className="flex flex-col gap-5" data-testid="home">
      <h1 className="sr-only">{t("nav.home")}</h1>
      <section className="grid gap-5 md:grid-cols-2 lg:grid-cols-3">
        <DailyTile domain="music" card={dailies.find((c) => c.domain === "music")} />
        <DailyTile domain="film" card={dailies.find((c) => c.domain === "film")} />
        <ProgressTile progress={blockData(blocks.progress)} isUser={isUser} />
      </section>
      <DailyFeed isUser={isUser} />
      <section
        className={cn(
          "grid items-start gap-5 sm:grid-cols-2",
          event ? "lg:grid-cols-4" : "lg:grid-cols-3",
        )}
      >
        <SoloTile modes={modes} />
        <OnlineTile isUser={isUser} />
        <CollectionsTile />
        {event && <EventTile event={event} />}
      </section>
    </div>
  );
}
