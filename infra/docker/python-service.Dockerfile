# syntax=docker/dockerfile:1.7
# Единый образ для любого Python-сервиса Guessverse.
#   docker build -f infra/docker/python-service.Dockerfile --build-arg SERVICE=game -t guessverse/game .
# Процессы одного образа: api | relay | consumer | worker | all | migrate (аргумент CMD).

ARG PYTHON_VERSION=3.13

# ------------------------------------------------------------------ сборка зависимостей
FROM python:${PYTHON_VERSION}-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv

ARG SERVICE
RUN test -n "${SERVICE}" || (echo "укажите --build-arg SERVICE=<имя>" && exit 1)

WORKDIR /src
# Workspace целиком: uv нужен состав участников, чтобы собрать нужный пакет по lock-файлу.
COPY pyproject.toml uv.lock ./
COPY libs ./libs
COPY tools ./tools
COPY services ./services

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable \
        --package "guessverse-$(echo "${SERVICE}" | tr '_' '-')" \
 && ln -s "/app/.venv/bin/${SERVICE}" /app/.venv/bin/service-entry \
 && test -x /app/.venv/bin/service-entry

# ------------------------------------------------------------------ рантайм
FROM python:${PYTHON_VERSION}-slim AS runtime

# Системные пакеты сервиса из реестра (поле apt, например ffmpeg для media-pipeline).
ARG APT_PACKAGES=""
RUN if [ -n "${APT_PACKAGES}" ]; then \
      apt-get update \
      && apt-get install -y --no-install-recommends ${APT_PACKAGES} \
      && rm -rf /var/lib/apt/lists/*; \
    fi

RUN groupadd --gid 10001 app \
 && useradd --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app

# Только виртуальное окружение: исходники и инструменты сборки в образ не попадают.
COPY --from=builder /app/.venv /app/.venv

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER 10001:10001
EXPOSE 8000
ENTRYPOINT ["service-entry"]
CMD ["all"]
