import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";
import { UnsubscribeView } from "@/features/notifications/UnsubscribeView";

// Токен отписки приходит в адресе: страница клиентская и динамическая, `searchParams` на сервере
// не читаются (токен не попадает в серверный рендер и логи Next).
export const dynamic = "force-dynamic";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations();
  return {
    title: t("unsubscribe.title"),
    // Ссылка с токеном не должна утекать в Referer ни при переходах, ни при загрузке ресурсов.
    referrer: "no-referrer",
    robots: { index: false, follow: false },
  };
}

export default function UnsubscribePage() {
  return <UnsubscribeView />;
}
