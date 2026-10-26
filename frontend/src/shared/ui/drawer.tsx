"use client";
/** Панель, выезжающая слева (меню): 300 px, на телефоне — на всю ширину. Escape и клик по фону закрывают. */
import { useRef, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { cn } from "@/shared/lib/cn";
import { useOverlay } from "./use-overlay";

type Props = {
  open: boolean;
  onClose: () => void;
  "aria-label": string;
  children: ReactNode;
  className?: string;
};

export function Drawer({ open, onClose, children, className, "aria-label": label }: Props) {
  const ref = useRef<HTMLElement>(null);
  useOverlay(open, onClose, ref);
  if (!open || typeof document === "undefined") return null;
  return createPortal(
    <div className="fixed inset-0 z-40 flex bg-night/70">
      <aside
        ref={ref}
        role="dialog"
        aria-modal="true"
        aria-label={label}
        tabIndex={-1}
        className={cn(
          "flex h-full w-[300px] max-w-full animate-rise flex-col gap-1 overflow-y-auto border-r-[1.5px] border-rule bg-stage p-4 max-sm:w-full",
          className,
        )}
      >
        {children}
      </aside>
      <div aria-hidden className="flex-1 max-sm:hidden" onClick={onClose} />
    </div>,
    document.body,
  );
}
