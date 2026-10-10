# Веб-клиент OnCue (Next.js, output: standalone). Контекст сборки — каталог frontend/:
#   docker build -f infra/docker/web.Dockerfile frontend
# Типы API (src/shared/api/schema) закоммичены и сверяются в CI (`pnpm gen:api:check`),
# поэтому контракты в контекст не нужны.
ARG NODE_VERSION=24-alpine

FROM node:${NODE_VERSION} AS deps
WORKDIR /app
RUN corepack enable
COPY package.json pnpm-lock.yaml ./
RUN --mount=type=cache,id=pnpm,target=/root/.local/share/pnpm/store \
    pnpm install --frozen-lockfile

FROM node:${NODE_VERSION} AS build
WORKDIR /app
RUN corepack enable
ENV NEXT_TELEMETRY_DISABLED=1
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN pnpm build

FROM node:${NODE_VERSION} AS runtime
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    HOSTNAME=0.0.0.0 \
    PORT=3000
# Только результат standalone-сборки: сервер, статика; без исходников и dev-зависимостей.
COPY --from=build --chown=node:node /app/.next/standalone ./
COPY --from=build --chown=node:node /app/.next/static ./.next/static
USER node
EXPOSE 3000
HEALTHCHECK --interval=10s --timeout=3s --start-period=15s --retries=6 \
    CMD wget -qO- http://127.0.0.1:3000/healthz >/dev/null || exit 1
# server.js сам обрабатывает SIGTERM (graceful shutdown Next).
CMD ["node", "server.js"]
