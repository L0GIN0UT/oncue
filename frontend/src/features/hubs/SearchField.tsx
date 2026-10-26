/** Поле поиска по макету «Сборники»: высота 56, иконка лупы слева, подпись только для чтения с экрана. */
import { forwardRef, type ComponentProps } from "react";
import { cn } from "@/shared/lib/cn";
import { useIcons } from "@/shared/ui/theme-provider";

type Props = Omit<ComponentProps<"input">, "type"> & {
  "aria-label": string;
  /** Класс обёртки (ширина, отступы): у самого поля — `className`. */
  wrapperClassName?: string;
};

export const SearchField = forwardRef<HTMLInputElement, Props>(function SearchField(
  { className, wrapperClassName, ...props },
  ref,
) {
  const Search = useIcons().spot;
  return (
    <div className={cn("relative flex items-center", wrapperClassName)}>
      <Search aria-hidden className="pointer-events-none absolute left-3.5 size-4 text-ink-muted" />
      <input
        ref={ref}
        type="search"
        autoComplete="off"
        spellCheck={false}
        className={cn(
          "h-14 w-full rounded-[var(--radius-control)] border-[1.5px] border-rule bg-stage pr-4 pl-12 text-base text-ink outline-none placeholder:text-ink-faint focus:border-music focus:shadow-[0_0_0_4px_var(--color-glow)]",
          className,
        )}
        {...props}
      />
    </div>
  );
});
