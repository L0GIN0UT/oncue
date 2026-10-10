import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";
import { AuthShell } from "@/features/auth/AuthShell";
import { VerifyEmail } from "@/features/auth/AuthForms";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations();
  return { title: t("auth.verify.title"), robots: { index: false } };
}

export default async function VerifyEmailPage({
  searchParams,
}: {
  searchParams: Promise<{ token?: string | string[] }>;
}) {
  const [t, query] = await Promise.all([getTranslations(), searchParams]);
  const token = typeof query.token === "string" && query.token.length <= 256 ? query.token : null;
  return (
    <AuthShell title={t("auth.verify.title")}>
      <VerifyEmail token={token} />
    </AuthShell>
  );
}
