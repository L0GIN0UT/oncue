import type { NextConfig } from "next";
import createNextIntlPlugin from "next-intl/plugin";

const withNextIntl = createNextIntlPlugin("./src/shared/i18n/request.ts");

/** Сборка standalone: контейнер `web` за gateway (`location /`), см. infra/docker/web.Dockerfile. */
const nextConfig: NextConfig = {
  output: "standalone",
  poweredByHeader: false,
  reactStrictMode: true,
  // CSP с nonce ставит proxy (src/proxy.ts); остальные заголовки безопасности — nginx (web-headers.conf).
  async redirects() {
    // Ссылки из писем identity по умолчанию ведут на /auth/... (GV_EMAIL__VERIFY_LINK).
    return [{ source: "/auth/verify-email", destination: "/verify-email", permanent: false }];
  },
};

export default withNextIntl(nextConfig);
