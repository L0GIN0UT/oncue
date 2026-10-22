import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";
import { AuthShell } from "@/features/auth/AuthShell";
import { ChangePasswordForm } from "@/features/auth/PasswordForms";

export const dynamic = "force-dynamic";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations();
  return { title: t("auth.security.title"), robots: { index: false } };
}

export default async function SecurityPage() {
  const t = await getTranslations();
  return (
    <AuthShell title={t("auth.security.title")} lead={t("auth.security.lead")}>
      <ChangePasswordForm />
    </AuthShell>
  );
}
