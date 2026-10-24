import { useTranslations } from "next-intl";
import type { FriendStatus } from "@/shared/api/types";
import { cn } from "@/shared/lib/cn";

const DOT: Record<FriendStatus, string> = {
  online: "bg-exact",
  in_match: "bg-partial",
  offline: "bg-ink-faint",
};

/** Статус друга: цветная точка плюс текст (цвет не единственный носитель смысла). */
export function StatusDot({ status }: { status: FriendStatus }) {
  const t = useTranslations();
  return (
    <span className="inline-flex items-center gap-2 text-sm text-ink-muted" data-status={status}>
      <span aria-hidden className={cn("size-2.5 rounded-full", DOT[status])} />
      {t(`social.status.${status}`)}
    </span>
  );
}
