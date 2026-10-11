import type { Metadata, Viewport } from "next";
import type { ReactNode } from "react";
import { headers } from "next/headers";
import { NextIntlClientProvider } from "next-intl";
import { getTranslations } from "next-intl/server";
import "@fontsource-variable/onest";
import "@fontsource-variable/unbounded";
import "./globals.css";
import { Providers } from "@/shared/ui/providers";
import { SiteHeader } from "@/features/auth/SiteHeader";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations();
  return {
    title: { default: t("meta.title"), template: `%s · OnCue` },
    description: t("meta.description"),
  };
}

export const viewport: Viewport = { themeColor: "#16112a", colorScheme: "dark" };

export default async function RootLayout({ children }: { children: ReactNode }) {
  // Чтение заголовков делает рендер динамическим: nonce CSP (src/proxy.ts) нужен на каждый ответ.
  await headers();
  const t = await getTranslations();
  return (
    <html lang="ru" className="dark">
      <body className="flex min-h-dvh flex-col antialiased">
        <NextIntlClientProvider>
          <Providers>
            <a
              href="#main"
              className="sr-only rounded-md bg-ink px-3 py-2 text-night focus:not-sr-only focus:fixed focus:top-2 focus:left-2 focus:z-50"
            >
              {t("nav.skip")}
            </a>
            <SiteHeader />
            <main
              id="main"
              className="mx-auto flex w-full max-w-5xl flex-1 flex-col px-4 pb-16 sm:px-6"
            >
              {children}
            </main>
          </Providers>
        </NextIntlClientProvider>
      </body>
    </html>
  );
}
