"use client";
/**
 * Обложка сборника — пресет из `GET /v1/meta` (градиент и эмодзи из конфига, постеров нет, D6).
 * Цвета берутся только в виде `#rrggbb`: в атрибут `style` не попадает произвольная строка.
 */
import type { CoverPreset } from "@/shared/api/types";
import { cn } from "@/shared/lib/cn";
import { useCollectionsMeta } from "./api";

const HEX = /^#[0-9a-fA-F]{6}$/;
/** Запасной градиент — токены темы (ADR 0049), а не литералы. */
const FALLBACK = ["var(--color-stage-raised)", "var(--color-rule)"];

export function coverGradient(preset: CoverPreset | undefined): string {
  const colors = (preset?.gradient ?? []).filter((c) => HEX.test(c));
  const stops =
    colors.length >= 2 ? colors : colors.length === 1 ? [colors[0]!, colors[0]!] : FALLBACK;
  return `linear-gradient(135deg, ${stops.join(", ")})`;
}

export function usePreset(id: string | null | undefined): CoverPreset | undefined {
  const meta = useCollectionsMeta();
  return id ? meta.data?.cover_presets?.find((p) => p.id === id) : undefined;
}

export function Cover({
  preset,
  className,
  size = "md",
}: {
  preset: string | null | undefined;
  className?: string;
  size?: "sm" | "md" | "lg";
}) {
  const p = usePreset(preset);
  return (
    <div
      aria-hidden
      className={cn(
        "grid shrink-0 place-items-center rounded-[var(--radius-control)] ring-1 ring-rule",
        size === "sm" && "size-12 text-2xl",
        size === "md" && "aspect-[4/3] w-full text-4xl",
        size === "lg" && "aspect-[4/3] w-full text-6xl sm:w-64",
        className,
      )}
      style={{ background: coverGradient(p) }}
    >
      <span>{p?.emoji ?? "🎞️"}</span>
    </div>
  );
}
