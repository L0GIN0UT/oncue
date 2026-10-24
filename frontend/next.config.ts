import type { NextConfig } from "next";
import createNextIntlPlugin from "next-intl/plugin";

const withNextIntl = createNextIntlPlugin("./src/shared/i18n/request.ts");

/** Сборка standalone: контейнер `web` за gateway (`location /`), см. infra/docker/web.Dockerfile. */
const nextConfig: NextConfig = {
  output: "standalone",
  poweredByHeader: false,
  reactStrictMode: true,
  async headers() {
    // Токен отписки лежит в адресе страницы: Referer с ним никуда не уходит (дублирует meta страницы).
    return [
      { source: "/unsubscribe", headers: [{ key: "Referrer-Policy", value: "no-referrer" }] },
    ];
  },
  // CSP с nonce ставит proxy (src/proxy.ts); остальные заголовки безопасности — nginx (web-headers.conf).
};

export default withNextIntl(nextConfig);
