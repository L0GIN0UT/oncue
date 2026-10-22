import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";
import { AuthShell } from "@/features/auth/AuthShell";
import { ForgotPasswordForm } from "@/features/auth/PasswordForms";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations();
  return { title: t("auth.forgotTitle"), robots: { index: false } };
}

export default async function ForgotPasswordPage() {
  const t = await getTranslations();
  return (
    <AuthShell title={t("auth.forgotTitle")} lead={t("auth.forgotLead")}>
      <ForgotPasswordForm />
    </AuthShell>
  );
}
