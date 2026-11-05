"use client";
import { useEffect } from "react";

/**
 * Заголовок вкладки, который известен только после загрузки данных (название сборника): ставится
 * на время жизни компонента, на уходе возвращается прежний. `null` — оставить как есть.
 */
export function useDocumentTitle(title: string | null): void {
  useEffect(() => {
    if (!title) return;
    const previous = document.title;
    document.title = title;
    return () => {
      document.title = previous;
    };
  }, [title]);
}
