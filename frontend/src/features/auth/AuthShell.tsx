/** Каркас страниц входа и регистрации (макет Login): плитка 440 px по центру с заголовком и формой. */
import type { ReactNode } from "react";
import { Tile } from "@/shared/ui/tile";

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
    <div className="mx-auto flex w-full max-w-[440px] flex-col pt-6 sm:pt-12">
      <Tile className="gap-3.5 px-[30px] py-7 max-sm:px-5">
        <h1 className="font-display text-[26px] leading-[1.1] font-extrabold tracking-[-0.02em]">
          {title}
        </h1>
        {lead && <p className="text-[13px] leading-[1.45] text-ink-muted">{lead}</p>}
        {children}
      </Tile>
    </div>
  );
}
