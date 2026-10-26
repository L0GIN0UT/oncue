"use client";
/**
 * Кнопка сохранения в ответе раунда: одна форма для обоих доменов, различаются подпись и значок
 * («В плейлист» с нотой и плюсом, «В закладки» с закладкой и плюсом). Ведёт в то же меню полок
 * `ShelfPicker`, что и `SaveButton`; гостю — ссылка на регистрацию.
 * Вынести в общие на этапе 2: у `SaveButton` (features/library) нет пропсов подписи и значка.
 */
import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import type { Domain } from "@/shared/api/types";
import { buttonVariants } from "@/shared/ui/button";
import { cn } from "@/shared/lib/cn";
import { useViewer } from "@/features/auth/session";
import { ShelfPicker } from "@/features/library/ShelfPicker";

const svg = {
  viewBox: "0 0 24 24",
  "aria-hidden": true,
  className:
    "size-[22px] shrink-0 fill-none stroke-current stroke-2 [stroke-linecap:round] [stroke-linejoin:round]",
} as const;

const ICON: Record<Domain, ReactNode> = {
  music: (
    <svg {...svg}>
      <path d="M9 18V6l8-1.6V15" />
      <circle cx="6.5" cy="18" r="2.5" />
      <circle cx="14.5" cy="15" r="2.5" />
      <path d="M19 3v6M16 6h6" />
    </svg>
  ),
  film: (
    <svg {...svg}>
      <path d="M6 4h12v17l-6-4-6 4z" />
      <path d="M12 8v6M9 11h6" />
    </svg>
  ),
};

export function RevealSave({
  mediaItemId,
  domain,
  className,
}: {
  mediaItemId: string;
  domain: Domain;
  className?: string;
}) {
  const t = useTranslations();
  const viewer = useViewer();
  const [open, setOpen] = useState(false);
  const wrapper = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const id = useId();
  const classes = cn(
    buttonVariants({ variant: "secondary", size: "md" }),
    "px-4 text-sm",
    className,
  );
  const label = t(`game.reveal.save.${domain}`);

  useEffect(() => {
    if (!open) return;
    const onPointer = (e: PointerEvent) => {
      if (!wrapper.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      // Escape закрывает только меню полок, а не весь ответ раунда.
      e.stopPropagation();
      setOpen(false);
      trigger.current?.focus();
    };
    document.addEventListener("pointerdown", onPointer);
    document.addEventListener("keydown", onKey, true);
    return () => {
      document.removeEventListener("pointerdown", onPointer);
      document.removeEventListener("keydown", onKey, true);
    };
  }, [open]);

  if (viewer.kind !== "user") {
    return (
      <Link
        href="/register"
        className={classes}
        data-testid="save-button"
        title={t("library.save.guest")}
        aria-label={t(`game.reveal.saveAria.${domain}`)}
      >
        {ICON[domain]}
        {label}
      </Link>
    );
  }
  return (
    <div ref={wrapper} className="relative inline-flex">
      <button
        ref={trigger}
        id={id}
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={t(`game.reveal.saveAria.${domain}`)}
        className={classes}
        data-testid="save-button"
        onClick={() => setOpen((v) => !v)}
      >
        {ICON[domain]}
        {label}
      </button>
      {open && (
        // Меню полок раскрывается вверх: внизу карточки ответа ему не хватает места.
        <div className="contents [&>div]:top-auto! [&>div]:bottom-full! [&>div]:mt-0! [&>div]:mb-2!">
          <ShelfPicker
            mediaItemId={mediaItemId}
            domain={domain}
            source="reveal"
            labelledBy={id}
            onClose={() => {
              setOpen(false);
              trigger.current?.focus();
            }}
          />
        </div>
      )}
    </div>
  );
}
