"use client";
/**
 * Игрок на экране: ник + аватар (своя одобренная аватарка, иначе предмет слота «аватар») + рамка
 * + титул под ником. Оформление приходит блоком `Block | null` — `null`, «недоступно» и пустые
 * слоты дают просто ник с буквой-заглушкой, ничего не падает. Ник и титул — данные игрока и
 * админки: выводятся только текстом (React экранирует).
 */
import type { ReactNode } from "react";
import { cn } from "@/shared/lib/cn";
import { CosmeticImage, safeImageUrl } from "./CosmeticImage";
import { itemOf, type AppearanceLike } from "./slots";

type Size = "sm" | "md" | "lg";

const AVATAR_SIZE: Record<Size, string> = {
  sm: "size-8 text-sm",
  md: "size-10 text-base",
  lg: "size-16 text-2xl",
};

type Props = {
  nickname: string;
  appearance?: AppearanceLike | null;
  size?: Size;
  /** Показывать титул под ником (по умолчанию да). */
  showTitle?: boolean;
  className?: string;
  /** Дополнение справа от ника в той же строке (корона хоста, «вы»). */
  suffix?: ReactNode;
};

export function Avatar({
  nickname,
  appearance,
  size = "md",
}: {
  nickname: string;
  appearance?: AppearanceLike | null;
  size?: Size;
}) {
  const custom = safeImageUrl(appearance?.custom_avatar_url);
  const avatar = itemOf(appearance, "avatar");
  const frame = itemOf(appearance, "frame");
  return (
    <span className={cn("relative inline-grid shrink-0 place-items-center", AVATAR_SIZE[size])}>
      {custom ? (
        // eslint-disable-next-line @next/next/no-img-element -- адрес из ответа progression, не статика сборки
        <img
          src={custom}
          alt=""
          loading="lazy"
          decoding="async"
          className="size-full rounded-full object-cover"
        />
      ) : avatar ? (
        <CosmeticImage item={avatar} decorative className="size-full rounded-full" />
      ) : (
        <span
          aria-hidden
          className="grid size-full place-items-center rounded-full bg-stage-raised font-display font-semibold text-ink-muted ring-1 ring-rule"
        >
          {Array.from(nickname.trim())[0]?.toUpperCase() ?? "?"}
        </span>
      )}
      {frame && (
        <CosmeticImage
          item={frame}
          decorative
          className="pointer-events-none absolute -inset-[12%] size-[124%] max-w-none object-contain"
        />
      )}
    </span>
  );
}

export function PlayerBadge({
  nickname,
  appearance,
  size = "md",
  showTitle = true,
  className,
  suffix,
}: Props) {
  const title = showTitle ? itemOf(appearance, "title") : null;
  return (
    <span data-testid="player-badge" className={cn("flex min-w-0 items-center gap-3", className)}>
      <Avatar nickname={nickname} appearance={appearance} size={size} />
      <span className="flex min-w-0 flex-col leading-tight">
        <span className="flex items-center gap-1.5 font-medium">
          <span className="truncate" dir="auto" data-testid="player-nickname">
            {nickname}
          </span>
          {suffix}
        </span>
        {title && (
          <span className="truncate text-xs text-ink-muted" dir="auto" data-testid="player-title">
            {title.title}
          </span>
        )}
      </span>
    </span>
  );
}

/** Карточка с фоном профиля из слота «фон»; без фона — просто контейнер. */
export function ProfileBackdrop({
  appearance,
  children,
  className,
}: {
  appearance?: AppearanceLike | null;
  children: ReactNode;
  className?: string;
}) {
  const background = itemOf(appearance, "background");
  return (
    <div
      className={cn("relative isolate overflow-hidden", className)}
      data-testid="profile-backdrop"
    >
      {background && (
        <CosmeticImage
          item={background}
          decorative
          className="pointer-events-none absolute inset-0 -z-10 size-full opacity-40"
        />
      )}
      {children}
    </div>
  );
}
