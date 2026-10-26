"use client";
/**
 * Переходы из профиля в разделы, которых нет в меню: магазин, друзья, рейтинг, медиатека, мои
 * сборники, безопасность. Временная плоская подборка этапа 0; экран профиля её переделает.
 */
import { useTranslations } from "next-intl";
import { Row } from "@/shared/ui/row";

const LINKS = [
  { href: "/shop", key: "shop", testId: "nav-shop" },
  { href: "/friends", key: "friends", testId: "nav-friends" },
  { href: "/leaderboards", key: "leaderboards", testId: "nav-leaderboards" },
  { href: "/library", key: "library", testId: "nav-library" },
  { href: "/me/collections", key: "myCollections", testId: "nav-my-collections" },
  { href: "/me/security", key: "security", testId: "nav-security" },
] as const;

export function ProfileLinks() {
  const t = useTranslations();
  return (
    <nav aria-label={t("nav.profileSections")} className="flex flex-col gap-2.5">
      {LINKS.map((l) => (
        <Row key={l.href} href={l.href} data-testid={l.testId}>
          {t(`nav.${l.key}`)}
        </Row>
      ))}
    </nav>
  );
}
