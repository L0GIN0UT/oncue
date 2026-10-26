"use client";
/** Колонка содержимого страницы: 1280 px для экранов v3 (`isWideRoute`), прежние 640 px для остальных. */
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";
import { cn } from "@/shared/lib/cn";
import { isWideRoute } from "./routes";

export function PageColumn({ children }: { children: ReactNode }) {
  const wide = isWideRoute(usePathname());
  return (
    <main
      id="main"
      className={cn(
        "mx-auto flex w-full flex-1 flex-col pt-2 pb-16",
        wide ? "max-w-[1280px] px-4 sm:px-6" : "max-w-[640px] px-4 sm:px-0",
      )}
    >
      {children}
    </main>
  );
}
