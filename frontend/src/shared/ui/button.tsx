/** Кнопка (по мотивам shadcn/ui): варианты через cva, `asChild` — для ссылок. */
import * as React from "react";
import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/shared/lib/cn";

const buttonVariants = cva(
  "inline-flex select-none items-center justify-center gap-2 whitespace-nowrap rounded-[var(--radius-control)] font-medium transition-[background-color,color,transform,box-shadow] duration-150 active:translate-y-px disabled:pointer-events-none disabled:opacity-45 [&_svg]:size-4 [&_svg]:shrink-0",
  {
    variants: {
      variant: {
        // Главное действие экрана: цвет домена (--accent), по умолчанию — латунь.
        primary:
          "bg-[var(--accent,var(--color-music))] text-night shadow-[0_6px_0_-2px_var(--accent-deep,var(--color-music-deep))] hover:brightness-110",
        secondary: "bg-stage-raised text-ink ring-1 ring-rule ring-inset hover:bg-rule/70",
        ghost: "text-ink-muted hover:bg-stage-raised hover:text-ink",
        quiet: "text-ink-muted underline-offset-4 hover:text-ink hover:underline",
      },
      size: {
        sm: "h-9 px-3 text-sm",
        md: "h-11 px-5 text-base",
        lg: "h-14 px-7 text-lg",
      },
    },
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
