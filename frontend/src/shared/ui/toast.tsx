"use client";
/** Короткие уведомления (429, сеть, «раунд не засчитан»): одна живая область `role="status"`. */
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";

type Toast = { id: number; text: string };
const ToastContext = createContext<(text: string) => void>(() => {});

const TOAST_MS = 4000;

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const seq = useRef(0);
  const push = useCallback((text: string) => {
    seq.current += 1;
    const id = seq.current;
    setToasts((list) => [...list.filter((t) => t.text !== text), { id, text }]);
  }, []);
  return (
    <ToastContext.Provider value={push}>
      {children}
      <div
        role="status"
        aria-live="polite"
        className="pointer-events-none fixed inset-x-0 bottom-4 z-50 flex flex-col items-center gap-2 px-4"
      >
        {toasts.map((t) => (
          <ToastItem
            key={t.id}
            toast={t}
            onDone={() => setToasts((l) => l.filter((x) => x.id !== t.id))}
          />
        ))}
      </div>
    </ToastContext.Provider>
  );
}

function ToastItem({ toast, onDone }: { toast: Toast; onDone: () => void }) {
  useEffect(() => {
    const timer = setTimeout(onDone, TOAST_MS);
    return () => clearTimeout(timer);
  }, [onDone]);
  return (
    <p className="pointer-events-auto max-w-md rounded-[var(--radius-control)] bg-ink px-4 py-2.5 text-sm font-medium text-night shadow-lg">
      {toast.text}
    </p>
  );
}

export function useToast(): (text: string) => void {
  return useContext(ToastContext);
}
