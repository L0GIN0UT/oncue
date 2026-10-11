/** Каркас страниц входа и регистрации: заголовок и узкая колонка формы. */
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
    <div className="mx-auto flex w-full max-w-md flex-col gap-8 pt-10 sm:pt-16">
      <header className="flex flex-col gap-2">
        <h1 className="font-display text-3xl font-bold">{title}</h1>
        {lead && <p className="text-ink-muted">{lead}</p>}
      </header>
      {children}
    </div>
  );
}
