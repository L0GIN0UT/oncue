import type { Metadata, Viewport } from "next";
import type { ReactNode } from "react";
import { headers } from "next/headers";
import { NextIntlClientProvider } from "next-intl";
import { getTranslations } from "next-intl/server";
import "@fontsource-variable/onest";
import "@fontsource-variable/unbounded";
import "./globals.css";
import { Providers } from "@/shared/ui/providers";
import { AppHeader } from "@/features/shell/AppHeader";
import { HeaderLeadingProvider } from "@/features/shell/header-leading";
import { SanctionsBanner } from "@/features/sanctions/SanctionsBanner";
import { getThemeKey } from "@/shared/api/shell";
import { ThemeProvider } from "@/shared/ui/theme-provider";
import { THEME_META } from "@/styles/theme";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations();
  return {
    title: { default: t("meta.title"), template: `%s · Guessverse` },
    description: t("meta.description"),
  };
}

export async function generateViewport(): Promise<Viewport> {
  return { themeColor: THEME_META[await getThemeKey()].themeColor, colorScheme: "dark" };
}

export default async function RootLayout({ children }: { children: ReactNode }) {
  // Чтение заголовков делает рендер динамическим: nonce CSP (src/proxy.ts) нужен на каждый ответ.
  await headers();
  const t = await getTranslations();
  // Ключ темы известен на сервере — `data-theme` в первом же HTML, без мигания (ADR 0049).
  const themeKey = await getThemeKey();
  return (
    <html lang="ru" className="dark" data-theme={themeKey}>
      <body className="flex min-h-dvh flex-col antialiased">
        <NextIntlClientProvider>
          <Providers>
            <ThemeProvider themeKey={themeKey}>
              <a
                href="#main"
                className="sr-only rounded-[var(--radius-control)] bg-ink px-3 py-2 text-night focus:not-sr-only focus:fixed focus:top-2 focus:left-2 focus:z-50"
              >
                {t("nav.skip")}
              </a>
              <HeaderLeadingProvider>
                <AppHeader />
                <SanctionsBanner />
                <main
                  id="main"
                  className="mx-auto flex w-full max-w-[640px] flex-1 flex-col px-4 pt-2 pb-16 sm:px-0"
                >
                  {children}
                </main>
              </HeaderLeadingProvider>
            </ThemeProvider>
          </Providers>
        </NextIntlClientProvider>
      </body>
    </html>
  );
}
