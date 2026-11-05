"use client";
/**
 * Жалоба на игрока из истории матчей: одна иконка-меню на строку, а не «Пожаловаться» у каждого
 * соперника. Пункты — игроки общего матча (`can_report`), пункт открывает обычный диалог жалобы
 * с контекстом матча (ADR 0069). Escape и клик вне меню закрывают его, фокус возвращается на иконку.
 */
import { useEffect, useId, useRef, useState } from "react";
import { useTranslations } from "next-intl";
import { Flag } from "lucide-react";
import { Button } from "@/shared/ui/button";
import { ReportDialog } from "./ReportDialog";

type Player = { user_id: string; nickname: string };

export function ReportPlayerMenu({
  matchId,
  players,
  testId,
}: {
  matchId: string;
  players: Player[];
  testId: string;
}) {
  const t = useTranslations();
  const menuId = useId();
  const [open, setOpen] = useState(false);
  const [picked, setPicked] = useState<Player | null>(null);
  const [done, setDone] = useState<"sent" | "repeated" | null>(null);
  const wrapper = useRef<HTMLSpanElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    const onPointer = (e: PointerEvent) => {
      if (!wrapper.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      setOpen(false);
      trigger.current?.focus();
    };
    document.addEventListener("pointerdown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  if (done)
    return (
      <span className="text-sm text-ink-muted" role="status" data-testid="report-done">
        {t(done === "sent" ? "social.report.sent" : "social.report.repeated")}
      </span>
    );

  return (
    <span ref={wrapper} className="relative inline-flex">
      <Button
        ref={trigger}
        variant="ghost"
        size="sm"
        aria-label={t("social.report.playerMenu")}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        title={t("social.report.playerMenu")}
        onClick={() => setOpen((v) => !v)}
        data-testid={testId}
      >
        <Flag aria-hidden />
      </Button>
      {open && (
        <div
          id={menuId}
          role="menu"
          aria-label={t("social.report.playerMenu")}
          className="absolute top-full right-0 z-20 mt-1 flex min-w-48 flex-col gap-1 rounded-[var(--radius-control)] border-[1.5px] border-rule bg-stage p-1.5 shadow-lg"
        >
          {players.map((p) => (
            <button
              key={p.user_id}
              type="button"
              role="menuitem"
              dir="auto"
              className="rounded-lg px-3 py-2 text-left text-sm hover:bg-stage-raised focus-visible:bg-stage-raised focus-visible:outline-none"
              onClick={() => {
                setOpen(false);
                setPicked(p);
              }}
              data-testid={`history-report-${p.nickname}`}
            >
              {t("social.report.playerLabel", { name: p.nickname })}
            </button>
          ))}
        </div>
      )}
      {picked && (
        <ReportDialog
          kind="player"
          targetId={picked.user_id}
          contextMatchId={matchId}
          onClose={() => {
            setPicked(null);
            trigger.current?.focus();
          }}
          onDone={(repeated) => {
            setPicked(null);
            setDone(repeated ? "repeated" : "sent");
          }}
        />
      )}
    </span>
  );
}
