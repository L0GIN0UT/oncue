/** Аватар в круглой рамке 1,5 px: картинка или инициалы из имени. */
import { cn } from "@/shared/lib/cn";

const SIZES = {
  sm: "size-8 text-xs",
  md: "size-[38px] text-[13px]",
  lg: "size-16 text-lg",
} as const;

export function initialsOf(name: string | null | undefined): string {
  const parts = (name ?? "").trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return "?";
  const letters =
    parts.length === 1 ? [...parts[0]!].slice(0, 2) : parts.slice(0, 2).map((p) => [...p][0]!);
  return letters.join("").toUpperCase();
}

export function Avatar({
  name,
  src,
  size = "md",
  className,
}: {
  name: string | null | undefined;
  src?: string | null;
  size?: keyof typeof SIZES;
  className?: string;
}) {
  return (
    <span
      className={cn(
        "grid shrink-0 place-items-center overflow-hidden rounded-full border-[1.5px] border-rule bg-stage font-semibold text-ink",
        SIZES[size],
        className,
      )}
    >
      {src ? (
        // Аватар приходит с бэкенда (media), размеры заранее неизвестны: оптимизатор Next не нужен.
        // eslint-disable-next-line @next/next/no-img-element
        <img src={src} alt="" className="size-full object-cover" />
      ) : (
        <span aria-hidden>{initialsOf(name)}</span>
      )}
    </span>
  );
}
