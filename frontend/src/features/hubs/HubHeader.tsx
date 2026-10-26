/** Заголовок экрана-хаба по макетам: слева крупное название, справа короткая подпись или ссылка. */
import type { ReactNode } from "react";

export function HubHeader({ title, aside }: { title: string; aside?: ReactNode }) {
  return (
    <div className="flex items-end justify-between gap-3 px-0.5">
      <h1 className="font-display text-[28px] leading-none font-extrabold tracking-[-0.03em]">
        {title}
      </h1>
      {aside != null && <span className="text-sm text-ink-muted">{aside}</span>}
    </div>
  );
}
