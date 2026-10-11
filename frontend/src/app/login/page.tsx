import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";
import { AuthShell } from "@/features/auth/AuthShell";
import { LoginForm } from "@/features/auth/AuthForms";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations();
  return { title: t("auth.signIn") };
}

export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<{ next?: string | string[] }>;
}) {
  const [t, query] = await Promise.all([getTranslations(), searchParams]);
  const next = typeof query.next === "string" ? query.next : null;
  return (
    <AuthShell title={t("auth.signInTitle")} lead={t("auth.signInLead")}>
      <LoginForm next={next} />
    </AuthShell>
  );
}
