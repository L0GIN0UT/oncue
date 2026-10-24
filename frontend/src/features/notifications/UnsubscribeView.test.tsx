/**
 * Отписка по ссылке из письма: токен в URL — чувствительное значение. Он не должен остаться
 * в адресной строке, уйти в Referer, в логи (console.*) или в адрес запроса; на любую «плохую»
 * ссылку — одно и то же сообщение. Сканеры почты открывают ссылки без взаимодействия, поэтому
 * POST уходит только по клику «Отписаться».
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { server, setupMockBackend, world } from "@/test/msw";
import { renderWithProviders } from "@/test/render";
import { UnsubscribeView, isPlausibleToken } from "./UnsubscribeView";

setupMockBackend();

const GOOD = "good-token-0123456789";
const OPAQUE = "Zm9vYmFyYmF6cXV4X3NlY3JldF90b2tlbl9fMDEyMzQ1Njc4OQ";

const open = (query: string) => window.history.pushState({}, "", `/unsubscribe${query}`);
const result = () => screen.getByTestId("unsubscribe-result");
const confirm = () => userEvent.click(screen.getByTestId("unsubscribe-confirm"));

const consoleSpies = () =>
  (["log", "info", "warn", "error", "debug"] as const).map((m) => vi.spyOn(console, m));

beforeEach(() => open(""));
afterEach(() => {
  vi.restoreAllMocks();
  open("");
});

describe("isPlausibleToken", () => {
  it.each([OPAQUE, GOOD, "a".repeat(16), "a".repeat(512), "A_b-C".repeat(5)])(
    "принимает %s",
    (t) => {
      expect(isPlausibleToken(t)).toBe(true);
    },
  );

  it.each([
    null,
    "",
    "a".repeat(15),
    "a".repeat(513),
    "tok en-0123456789ab",
    "tok.en-0123456789a",
    "токен-0123456789abcd",
    "a=b-0123456789abcdef",
  ])("отклоняет %j", (t) => {
    expect(isPlausibleToken(t)).toBe(false);
  });
});

describe("UnsubscribeView", () => {
  it("без клика запрос не уходит (сканеры почты не отписывают)", async () => {
    open(`?token=${GOOD}`);
    renderWithProviders(<UnsubscribeView />);
    expect(await screen.findByTestId("unsubscribe-confirm")).toBeInTheDocument();
    expect(result()).toHaveAttribute("data-state", "ready");
    await new Promise((r) => setTimeout(r, 100));
    expect(world.notifications.unsubscribeBodies).toEqual([]);
  });

  it("клик → один POST → «Вы отписаны»; повторный клик невозможен", async () => {
    open(`?token=${GOOD}`);
    renderWithProviders(<UnsubscribeView />);
    await confirm();
    await waitFor(() => expect(result()).toHaveAttribute("data-state", "done"));
    expect(result()).toHaveTextContent("Вы отписаны");
    expect(world.notifications.unsubscribeBodies).toEqual([GOOD]);
    expect(screen.queryByTestId("unsubscribe-confirm")).toBeNull();
  });

  it("двойной клик — один запрос", async () => {
    open(`?token=${GOOD}`);
    renderWithProviders(<UnsubscribeView />);
    const button = await screen.findByTestId("unsubscribe-confirm");
    await userEvent.dblClick(button);
    await waitFor(() => expect(result()).toHaveAttribute("data-state", "done"));
    expect(world.notifications.unsubscribeBodies).toHaveLength(1);
  });

  it("токен убран из адресной строки сразу при показе, до клика", async () => {
    open(`?token=${GOOD}&utm=1`);
    renderWithProviders(<UnsubscribeView />);
    expect(window.location.search).toBe("");
    expect(window.location.href).not.toContain(GOOD);
    await screen.findByTestId("unsubscribe-confirm");
    // Страница перерисована — токена в адресе по-прежнему нет.
    expect(window.location.href).not.toContain(GOOD);
  });

  it("токен уходит только в теле POST: ни в адресе запроса, ни в Referer", async () => {
    open(`?token=${OPAQUE}`);
    const seen: { url: string; referrer: string; body: unknown }[] = [];
    server.use(
      http.post("*/api/notifications/v1/unsubscribe", async ({ request }) => {
        seen.push({ url: request.url, referrer: request.referrer, body: await request.json() });
        return new HttpResponse(null, { status: 204 });
      }),
    );
    renderWithProviders(<UnsubscribeView />);
    await confirm();
    await waitFor(() => expect(result()).toHaveAttribute("data-state", "done"));
    expect(seen).toHaveLength(1);
    expect(seen[0]!.url).not.toContain(OPAQUE);
    expect(seen[0]!.referrer).not.toContain(OPAQUE);
    expect(seen[0]!.body).toEqual({ token: OPAQUE });
  });

  it("токен не попадает в console.*, в том числе при ошибках", async () => {
    const spies = consoleSpies();
    const bad = "secret-token-1234567890";
    open(`?token=${bad}`);
    renderWithProviders(<UnsubscribeView />);
    await confirm();
    await waitFor(() => expect(result()).toHaveAttribute("data-state", "invalid"));
    open("?token=down-token-0123456789");
    renderWithProviders(<UnsubscribeView />);
    await confirm();
    await waitFor(() =>
      expect(screen.getAllByTestId("unsubscribe-result")[1]).toHaveAttribute("data-state", "later"),
    );
    for (const spy of spies)
      for (const call of spy.mock.calls) {
        const text = JSON.stringify(call);
        expect(text).not.toContain(bad);
        expect(text).not.toContain("down-token");
      }
  });

  it("любой 422 — одно и то же сообщение, причину не различаем", async () => {
    const texts: (string | null)[] = [];
    for (const token of ["expired-token-0123456", "forged-token-01234567", "x".repeat(43)]) {
      open(`?token=${token}`);
      const view = renderWithProviders(<UnsubscribeView />);
      await confirm();
      await waitFor(() => expect(result()).toHaveAttribute("data-state", "invalid"));
      texts.push(result().textContent);
      view.unmount();
    }
    expect(new Set(texts).size).toBe(1);
    expect(texts[0]).toContain("Ссылка недействительна или устарела");
  });

  it.each([["no token"], ["short"], ["bad chars"]])(
    "нет токена или он неправдоподобен (%s) — то же сообщение, кнопки и запроса нет",
    async (kind) => {
      open(
        kind === "no token"
          ? ""
          : kind === "short"
            ? "?token=abc"
            : "?token=%3Cscript%3E-0123456789ab",
      );
      renderWithProviders(<UnsubscribeView />);
      expect(await screen.findByText(/Ссылка недействительна или устарела/)).toBeInTheDocument();
      expect(result()).toHaveAttribute("data-state", "invalid");
      expect(screen.queryByTestId("unsubscribe-confirm")).toBeNull();
      expect(world.notifications.unsubscribeBodies).toEqual([]);
    },
  );

  it.each([["limited-token-0123456789"], ["down-token-0123456789"]])(
    "429/503 (%s) — «повторите позже» и повтор с тем же токеном",
    async (token) => {
      open(`?token=${token}`);
      renderWithProviders(<UnsubscribeView />);
      await confirm();
      await waitFor(() => expect(result()).toHaveAttribute("data-state", "later"));
      expect(window.location.search).toBe("");
      server.use(
        http.post(
          "*/api/notifications/v1/unsubscribe",
          () => new HttpResponse(null, { status: 204 }),
        ),
      );
      await userEvent.click(screen.getByRole("button", { name: "Повторить" }));
      await waitFor(() => expect(result()).toHaveAttribute("data-state", "done"));
    },
  );

  it("в StrictMode токен не теряется и запрос уходит один раз по клику", async () => {
    open(`?token=${GOOD}`);
    const { StrictMode } = await import("react");
    renderWithProviders(
      <StrictMode>
        <UnsubscribeView />
      </StrictMode>,
    );
    await confirm();
    await waitFor(() => expect(result()).toHaveAttribute("data-state", "done"));
    expect(world.notifications.unsubscribeBodies).toEqual([GOOD]);
  });
});
