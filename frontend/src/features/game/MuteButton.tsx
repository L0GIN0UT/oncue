"use client";
/** Кнопка звука в оболочке игры: круглая, 40 px; состояние — `use-muted`. */
import { useTranslations } from "next-intl";
import { Volume2, VolumeX } from "lucide-react";
import { setMuted, useMuted } from "./use-muted";

export function MuteButton() {
  const t = useTranslations();
  const muted = useMuted();
  const Icon = muted ? VolumeX : Volume2;
  return (
    <button
      type="button"
      aria-pressed={muted}
      aria-label={t(muted ? "game.unmute" : "game.mute")}
      onClick={() => setMuted(!muted)}
      data-testid="mute"
      className="grid size-11 place-items-center rounded-full border-[1.5px] border-rule bg-stage text-ink-muted transition-colors hover:border-rule-strong hover:text-ink"
    >
      <Icon className="size-[22px]" aria-hidden />
    </button>
  );
}
