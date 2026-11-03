"use client";
/**
 * Картинка ответа: обложка 1:1 у музыки, постер 2:3 у кино (`artwork` из ответа game/match,
 * ADR 0073). Нет картинки (`null`), ссылка не открылась или истекла — заглушка с иконкой домена.
 */
import { useTranslations } from "next-intl";
import { Film, Music } from "lucide-react";
import type { Domain } from "@/shared/api/types";
import { cn } from "@/shared/lib/cn";
import { Cover } from "@/shared/ui/cover";

type Props = {
  domain: Domain;
  /** `reveal.artwork`: подписанная короткоживущая ссылка или `null`. */
  artwork?: { url: string } | null;
  /** Название ответа — для подписи картинки скринридеру; без него картинка декоративная. */
  title?: string;
  className?: string;
};

export function AnswerArt({ domain, artwork, title, className }: Props) {
  const t = useTranslations();
  const Icon = domain === "film" ? Film : Music;
  return (
    <Cover
      src={artwork?.url ?? null}
      alt={title ? t(`game.reveal.artAlt.${domain}`, { title }) : ""}
      ratio={domain === "film" ? "poster" : "square"}
      placeholder={<Icon className="size-12" aria-hidden />}
      className={cn("rounded-[14px]", domain === "film" ? "w-[180px]" : "w-[220px]", className)}
    />
  );
}
