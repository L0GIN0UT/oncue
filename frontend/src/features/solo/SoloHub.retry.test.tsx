import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, screen } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { RETRY_UNAVAILABLE_MS } from "@/shared/api/page-blocks";
import { server, setupMockBackend, signInAsGuest } from "@/test/msw";
import { renderWithProviders } from "@/test/render";
import { SoloHub } from "./SoloHub";

setupMockBackend();

const down = { status: "unavailable", data: null };
/** Страница solo со сбоем витрины режимов (остальные блоки пусты, но исправны). */
const brokenPage = () =>
  HttpResponse.json({
    blocks: {
      daily: { status: "ok", data: [] },
      weekly: { status: "ok", data: [] },
      random: { status: "ok", data: [] },
      modes: down,
      records: null,
    },
  });

beforeEach(() => {
  signInAsGuest();
  vi.useFakeTimers({ shouldAdvanceTime: true, toFake: ["setTimeout", "clearTimeout"] });
});
afterEach(() => vi.useRealTimers());

const advanceRetry = () =>
  act(async () => {
    await vi.advanceTimersByTimeAsync(RETRY_UNAVAILABLE_MS + 50);
  });

describe("соло-страница: блок «недоступно»", () => {
  it("витрина погасла — один повтор, и режимы появляются", async () => {
    let calls = 0;
    server.use(
      http.get(
        "*/api/web/v1/pages/solo",
        () => {
          calls += 1;
          return brokenPage();
        },
        { once: true },
      ),
    );
    renderWithProviders(<SoloHub initial={null} />);
    expect(await screen.findByText(/Режимы сейчас не загрузились/)).toBeInTheDocument();
    await advanceRetry();
    expect(await screen.findByTestId("mode-card-music.random.classic")).toBeInTheDocument();
    expect(screen.queryByText(/Режимы сейчас не загрузились/)).not.toBeInTheDocument();
    expect(calls).toBe(1);
  });

  it("повтор тоже неудачен — больше не запрашивает, остаётся «недоступно»", async () => {
    let calls = 0;
    server.use(
      http.get("*/api/web/v1/pages/solo", () => {
        calls += 1;
        return brokenPage();
      }),
    );
    renderWithProviders(<SoloHub initial={null} />);
    await screen.findByText(/Режимы сейчас не загрузились/);
    await advanceRetry();
    await advanceRetry();
    expect(calls).toBe(2);
    expect(screen.getByText(/Режимы сейчас не загрузились/)).toBeInTheDocument();
  });
});
