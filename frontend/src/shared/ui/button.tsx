/** Кнопка «Ночного эфира»: варианты через cva, `asChild` — для ссылок. Акцент только у primary. */
import * as React from "react";
import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/shared/lib/cn";

const buttonVariants = cva(
  "inline-flex select-none items-center justify-center gap-2 whitespace-nowrap border-[1.5px] font-semibold transition-[background-color,border-color,color] duration-150 disabled:pointer-events-none disabled:opacity-45 [&_svg]:size-[22px] [&_svg]:shrink-0",
  {
    variants: {
      variant: {
        // Главное действие экрана: одна на экран.
        primary:
          "rounded-[var(--radius-control)] border-music bg-music text-night hover:border-ink-soft hover:bg-ink-soft",
        secondary:
          "rounded-[var(--radius-control)] border-rule bg-stage text-ink hover:border-rule-strong",
        ghost:
          "rounded-[var(--radius-control)] border-transparent text-ink-muted hover:bg-stage-raised hover:text-ink",
        quiet:
          "rounded-[var(--radius-control)] border-transparent text-ink-muted underline-offset-4 hover:text-ink hover:underline",
        // Круглая кнопка-иконка 44 px (тап-цель); подпись — aria-label.
        icon: "size-11 rounded-full border-transparent p-0 text-ink-muted hover:border-rule hover:text-ink",
      },
      size: {
        sm: "h-11 px-4 text-sm",
        md: "h-[50px] px-[22px] text-[15px]",
        lg: "h-14 px-6 text-base",
      },
    },
    compoundVariants: [{ variant: "icon", className: "size-11 px-0" }],
    defaultVariants: { variant: "primary", size: "md" },
  },
);

export type ButtonProps = React.ComponentProps<"button"> &
  VariantProps<typeof buttonVariants> & { asChild?: boolean };

export function Button({ className, variant, size, asChild = false, ...props }: ButtonProps) {
  const Comp = asChild ? Slot : "button";
  return <Comp className={cn(buttonVariants({ variant, size }), className)} {...props} />;
}

export { buttonVariants };
