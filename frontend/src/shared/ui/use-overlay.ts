"use client";
/**
 * Поведение оверлея (модалка, панель меню): Escape закрывает, фокус уходит внутрь и возвращается
 * на прежний элемент, Tab не покидает оверлей, прокрутка страницы блокируется.
 */
import { useEffect, useRef, type RefObject } from "react";

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

export function useOverlay(open: boolean, onClose: () => void, ref: RefObject<HTMLElement | null>) {
  // Свежий onClose без пересоздания эффекта: иначе фокус прыгал бы при каждом рендере родителя.
  const close = useRef(onClose);
  useEffect(() => {
    close.current = onClose;
  });

  useEffect(() => {
    const root = ref.current;
    if (!open || !root) return;
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const first = root.querySelector<HTMLElement>(FOCUSABLE);
    (first ?? root).focus();
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";

    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") {
        e.stopPropagation();
        close.current();
        return;
      }
      if (e.key !== "Tab" || !root) return;
      const items = [...root.querySelectorAll<HTMLElement>(FOCUSABLE)];
      if (items.length === 0) {
        e.preventDefault();
        return;
      }
      const head = items[0]!;
      const tail = items[items.length - 1]!;
      if (e.shiftKey && document.activeElement === head) {
        e.preventDefault();
        tail.focus();
      } else if (!e.shiftKey && document.activeElement === tail) {
        e.preventDefault();
        head.focus();
      }
    }
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = overflow;
      previous?.focus();
    };
  }, [open, ref]);
}
