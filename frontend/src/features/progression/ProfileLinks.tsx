"use client";
/**
 * Переходы из профиля в разделы, которых нет в меню: медиатека, мои сборники, уведомления,
 * их настройки и безопасность. Магазин, друзья и рейтинг доступны из вкладок профиля.
 */
import { useTranslations } from "next-intl";
import { Row } from "@/shared/ui/row";

const LINKS = [
  { href: "/library", label: "nav.library", testId: "nav-library" },
  { href: "/me/collections", label: "nav.myCollections", testId: "nav-my-collections" },
  { href: "/notifications", label: "nav.notifications", testId: "nav-notifications" },
  {
    href: "/notifications/settings",
    label: "notifications.settingsLink",
    testId: "nav-notification-settings",
  },
  { href: "/me/security", label: "nav.security", testId: "nav-security" },
] as const;

export function ProfileLinks() {
  const t = useTranslations();
  return (
    <nav aria-label={t("progression.links.label")} className="flex flex-col gap-2.5">
      {LINKS.map((l) => (
        <Row key={l.href} href={l.href} data-testid={l.testId}>
          {t(l.label)}
        </Row>
      ))}
    </nav>
  );
}
