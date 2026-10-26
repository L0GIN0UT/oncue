/** Каркас страниц входа и регистрации: заголовок по центру и узкая колонка формы (420 px). */
import type { ReactNode } from "react";

export function AuthShell({
  title,
  lead,
  children,
}: {
  title: string;
  lead?: string;
  children: ReactNode;
}) {
  return (
    <div className="mx-auto flex w-full max-w-[420px] flex-col gap-7 pt-8 sm:pt-14">
      <header className="flex flex-col gap-2 text-center">
        <h1 className="font-display text-[28px] leading-[1.1] font-extrabold">{title}</h1>
        {lead && <p className="text-[15px] text-ink-muted">{lead}</p>}
      </header>
      {children}
    </div>
  );
}
