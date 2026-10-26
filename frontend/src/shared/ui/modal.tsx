"use client";
/** Модалка: диалог по центру поверх затемнения; Escape и клик по фону закрывают, фокус возвращается. */
import { useRef, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { cn } from "@/shared/lib/cn";
import { useOverlay } from "./use-overlay";

type Props = {
  open: boolean;
  onClose: () => void;
  /** Имя диалога для скринридера. */
  "aria-label": string;
  children: ReactNode;
  className?: string;
  "data-testid"?: string;
};

export function Modal({
  open,
  onClose,
  children,
  className,
  "aria-label": label,
  "data-testid": testId,
}: Props) {
  const ref = useRef<HTMLDivElement>(null);
  useOverlay(open, onClose, ref);
  if (!open || typeof document === "undefined") return null;
  return createPortal(
    <div className="fixed inset-0 z-40 grid place-items-center overflow-y-auto bg-night/70 p-4">
      <div aria-hidden className="absolute inset-0" onClick={onClose} />
      <div
        ref={ref}
        role="dialog"
        aria-modal="true"
        aria-label={label}
        tabIndex={-1}
        data-testid={testId}
        className={cn(
          "relative w-full max-w-[520px] animate-modal-in overflow-hidden rounded-[var(--radius-ticket)] border-[1.5px] border-rule bg-stage",
          className,
        )}
      >
        {children}
      </div>
    </div>,
    document.body,
  );
}
