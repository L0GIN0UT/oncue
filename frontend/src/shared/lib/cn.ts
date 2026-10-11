/** Склейка классов Tailwind с разрешением конфликтов (как в shadcn/ui). */
import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}
