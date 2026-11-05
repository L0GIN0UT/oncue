"use client";
/**
 * Кнопка «Сохранить» единицы (ревил, строка итога, карточки, D5). Пользователю открывает меню
 * полок ({@link ShelfPicker}); гостю и анониму медиатека недоступна (D7) — это ссылка на
 * регистрацию. Меню закрывается по Escape и клику вне его, фокус возвращается на кнопку.
 */
import { useEffect, useId, useRef, useState } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { Bookmark } from "lucide-react";
import type { Domain, SaveSource } from "@/shared/api/types";
import { buttonVariants } from "@/shared/ui/button";
import { cn } from "@/shared/lib/cn";
import { useViewer } from "@/features/auth/session";
import { ShelfPicker } from "./ShelfPicker";

type Props = {
  mediaItemId: string;
  source: SaveSource;
  domain?: Domain | null;
  className?: string;
  /** Только значок: у списка единиц подпись «Сохранить» одна на всех, а не на каждой строке. */
  compact?: boolean;
  /** Название единицы: имя кнопки в компактном виде («Сохранить «Creep»»). */
  itemTitle?: string;
};

export function SaveButton({ mediaItemId, source, domain, className, compact, itemTitle }: Props) {
  const t = useTranslations();
  const viewer = useViewer();
  const [open, setOpen] = useState(false);
  const wrapper = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const id = useId();
  const classes = cn(
    buttonVariants({ variant: "secondary", size: "sm" }),
    compact && "px-0 size-11",
    className,
  );
  const name = itemTitle
    ? t("library.save.labelFor", { title: itemTitle })
    : t("library.save.label");
  const content = (
    <>
      <Bookmark aria-hidden />
      {compact ? <span className="sr-only">{name}</span> : t("library.save.label")}
    </>
  );
  const compactProps = compact ? { "data-compact": "true", title: name } : {};

  useEffect(() => {
    if (!open) return;
    const close = (focus: boolean) => {
      setOpen(false);
      if (focus) trigger.current?.focus();
    };
    const onPointer = (e: PointerEvent) => {
      if (!wrapper.current?.contains(e.target as Node)) close(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") close(true);
    };
    document.addEventListener("pointerdown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  if (viewer.kind !== "user") {
    return (
      <Link
        href="/register"
        className={classes}
        data-testid="save-button"
        {...compactProps}
        title={compact ? `${name}. ${t("library.save.guest")}` : t("library.save.guest")}
      >
        {content}
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
        className={classes}
        data-testid="save-button"
        {...compactProps}
        onClick={() => setOpen((v) => !v)}
      >
        {content}
      </button>
      {open && (
        <ShelfPicker
          mediaItemId={mediaItemId}
          domain={domain}
          source={source}
          labelledBy={id}
          onClose={() => {
            setOpen(false);
            trigger.current?.focus();
          }}
        />
      )}
    </div>
  );
}
