import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { NAME_RETRY_MS } from "@/features/auth/session";
import { server, setupMockBackend, signInAsUser, world } from "@/test/msw";
import { renderWithProviders } from "@/test/render";
import { SiteHeader } from "./SiteHeader";

let pathname = "/collections/c1/edit";

vi.mock("next/navigation", () => ({ usePathname: () => pathname }));
vi.mock("@/features/notifications/NotificationBell", () => ({
  NotificationBell: () => <a href="/notifications">Уведомления</a>,
}));
vi.mock("@/features/progression/api", () => ({
  profileQueryKey: ["page", "profile"],
  fetchProfilePage: async () => ({ blocks: {} }),
}));

setupMockBackend();

/** Ответы `GET /v1/session` по порядку; последний повторяется. Возвращает счётчик запросов. */
function sessionResponses(...names: (string | null)[]) {
  let calls = 0;
  server.use(
    http.get("*/api/web/v1/session", () => {
      const name = names[Math.min(calls, names.length - 1)] ?? null;
      calls += 1;
      return HttpResponse.json({ subject: { kind: "user", display_name: name } });
    }),
  );
  return () => calls;
}

beforeEach(() => {
  pathname = "/collections/c1/edit";
  signInAsUser();
  vi.useFakeTimers({ shouldAdvanceTime: true, toFake: ["setTimeout", "clearTimeout"] });
});
afterEach(() => vi.useRealTimers());

const advanceRetry = () =>
  act(async () => {
    await vi.advanceTimersByTimeAsync(NAME_RETRY_MS + 50);
  });

describe("шапка: имя пользователя при сбое identity", () => {
  it("имя не пришло — скелетон, а не «Игрок»; через повтор появляется имя", async () => {
    const calls = sessionResponses(null, "Ника");
    renderWithProviders(<SiteHeader />);
    expect(await screen.findByTestId("user-name-skeleton")).toBeInTheDocument();
    expect(screen.queryByText("Игрок")).not.toBeInTheDocument();
    await advanceRetry();
    expect(await screen.findByTestId("user-name")).toHaveTextContent("Ника");
    expect(screen.queryByTestId("user-name-skeleton")).not.toBeInTheDocument();
    expect(calls()).toBe(2);
  });

  it("повтор тоже без имени — ровно один повтор, затем запасное «Игрок»", async () => {
    const calls = sessionResponses(null);
    renderWithProviders(<SiteHeader />);
    await screen.findByTestId("user-name-skeleton");
    await advanceRetry();
    expect(await screen.findByTestId("user-name")).toHaveTextContent("Игрок");
    await advanceRetry();
    expect(calls()).toBe(2);
  });

  it("возврат на вкладку при пользователе без имени перезапрашивает сессию", async () => {
    const calls = sessionResponses(null, null, "Ника");
    renderWithProviders(<SiteHeader />);
    await screen.findByTestId("user-name-skeleton");
    await advanceRetry();
    await screen.findByText("Игрок");
    act(() => void window.dispatchEvent(new Event("visibilitychange")));
    await waitFor(() => expect(screen.getByTestId("user-name")).toHaveTextContent("Ника"));
    expect(calls()).toBe(3);
  });

  it("несколько экземпляров шапки: повтор один на всё приложение", async () => {
    const calls = sessionResponses(null, "Ника");
    renderWithProviders(
      <>
        <SiteHeader />
        <SiteHeader />
      </>,
    );
    await screen.findAllByTestId("user-name-skeleton");
    await advanceRetry();
    expect(await screen.findAllByTestId("user-name")).toHaveLength(2);
    expect(calls()).toBe(2);
  });

  it("переход со страницы редактора на главную сохраняет имя в шапке", async () => {
    sessionResponses("Ника");
    const view = renderWithProviders(<SiteHeader />);
    expect(await screen.findByTestId("user-name")).toHaveTextContent("Ника");
    pathname = "/";
    view.rerender(<SiteHeader />);
    expect(screen.getByTestId("user-name")).toHaveTextContent("Ника");
    expect(screen.queryByTestId("user-name-skeleton")).not.toBeInTheDocument();
  });
});

describe("шапка: аноним при живой сессии", () => {
  it("аноним остался анонимом — refresh один раз, без цикла", async () => {
    server.use(http.get("*/api/web/v1/session", () => HttpResponse.json({ subject: null })));
    renderWithProviders(<SiteHeader />);
    await screen.findByRole("link", { name: "Войти" });
    await waitFor(() => expect(world.refreshCalls).toBe(1));
    await advanceRetry();
    await advanceRetry();
    expect(world.refreshCalls).toBe(1);
  });

  it("bff ответил «аноним», cookie сессии жива — один refresh и повторный запрос", async () => {
    let calls = 0;
    server.use(
      http.get("*/api/web/v1/session", () => {
        calls += 1;
        return HttpResponse.json({
          subject: calls === 1 ? null : { kind: "user", display_name: "Ника" },
        });
      }),
    );
    renderWithProviders(<SiteHeader />);
    expect(await screen.findByTestId("user-name")).toHaveTextContent("Ника");
    expect(world.refreshCalls).toBe(1);
    expect(calls).toBe(2);
  });
});
