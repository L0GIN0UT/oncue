import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";
import { AuthShell } from "@/features/auth/AuthShell";
import { RegisterForm } from "@/features/auth/AuthForms";
import { parseConsents } from "@/features/auth/consents";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations();
  return { title: t("auth.signUp") };
}

export default async function RegisterPage() {
  const t = await getTranslations();
  const consents = parseConsents(process.env.GV_CONSENTS__REQUIRED);
  return (
    <AuthShell title={t("auth.signUpTitle")} lead={t("auth.signUpLead")}>
      <RegisterForm consents={consents} />
    </AuthShell>
  );
}
