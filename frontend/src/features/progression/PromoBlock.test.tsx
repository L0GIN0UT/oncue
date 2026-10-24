import { describe, expect, it, vi } from "vitest";
import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { server, setupMockBackend, signInAsUser, world } from "@/test/msw";
import { renderWithProviders } from "@/test/render";
import { PromoBlock } from "./PromoBlock";

setupMockBackend();

const type = async (code: string) => {
  await userEvent.clear(screen.getByTestId("promo-input"));
  await userEvent.type(screen.getByTestId("promo-input"), code);
};

describe("PromoBlock", () => {
  it("код на Версы: награда показана, баланс мока вырос, ключ идемпотентности ушёл", async () => {
    signInAsUser();
    renderWithProviders(<PromoBlock />);
    await type("welcome");
    await userEvent.click(screen.getByTestId("promo-submit"));
    expect(await screen.findByTestId("promo-result")).toHaveTextContent("Начислено: 25 Версов");
    expect(world.economy.balance).toBe(65);
  });

  it("код на предмет", async () => {
    signInAsUser();
    renderWithProviders(<PromoBlock />);
    await type("FOXY");
    await userEvent.click(screen.getByTestId("promo-submit"));
    expect(await screen.findByTestId("promo-result")).toHaveTextContent("Лис");
  });

  it("повтор того же кода — награда та же, с пометкой", async () => {
    signInAsUser();
    renderWithProviders(<PromoBlock />);
    await type("welcome");
    await userEvent.click(screen.getByTestId("promo-submit"));
    await screen.findByTestId("promo-result");
    await type("welcome");
    await userEvent.click(screen.getByTestId("promo-submit"));
    await waitFor(() =>
      expect(screen.getByTestId("promo-result")).toHaveTextContent("уже использовали"),
    );
    expect(world.economy.balance).toBe(65);
  });

  it("любая причина отказа — одно сообщение 404", async () => {
    signInAsUser();
    renderWithProviders(<PromoBlock />);
    await type("nonsense");
    await userEvent.click(screen.getByTestId("promo-submit"));
    const first = (await screen.findByRole("alert")).textContent;
    expect(first).toContain("Код не подходит");
  });

  it("пустой ввод не уходит на сервер", async () => {
    signInAsUser();
    const spy = vi.fn();
    server.events.on("request:start", spy);
    renderWithProviders(<PromoBlock />);
    await userEvent.click(screen.getByTestId("promo-submit"));
    expect(await screen.findByRole("alert")).toHaveTextContent("Введите код");
    expect(spy.mock.calls.filter(([e]) => e.request.url.includes("promo"))).toHaveLength(0);
  });

  it("429: отсчёт по Retry-After, кнопка заблокирована, потом снова доступна", async () => {
    signInAsUser();
    world.scenario = { promoRateLimited: true };
    renderWithProviders(<PromoBlock />);
    await type("welcome");
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      await userEvent.click(screen.getByTestId("promo-submit"));
      const countdown = await screen.findByTestId("promo-countdown");
      expect(countdown).toHaveTextContent("3 секунды");
      expect(screen.getByTestId("promo-submit")).toBeDisabled();
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1000);
      });
      expect(screen.getByTestId("promo-countdown")).toHaveTextContent("2 секунды");
      await act(async () => {
        await vi.advanceTimersByTimeAsync(2500);
      });
      expect(screen.queryByTestId("promo-countdown")).toBeNull();
      expect(screen.getByTestId("promo-submit")).toBeEnabled();
    } finally {
      vi.useRealTimers();
    }
  });

  it("429 без Retry-After — общий текст", async () => {
    signInAsUser();
    server.use(
      http.post("*/api/progression/v1/promo/redeem", () =>
        HttpResponse.json(
          { code: "progression.promo_rate_limited", title: "x", status: 429 },
          { status: 429 },
        ),
      ),
    );
    renderWithProviders(<PromoBlock />);
    await type("welcome");
    await userEvent.click(screen.getByTestId("promo-submit"));
    expect(await screen.findByRole("alert")).toHaveTextContent("Слишком много попыток");
  });

  it("повтор после обрыва сети идёт с тем же Idempotency-Key", async () => {
    signInAsUser();
    const keys: (string | null)[] = [];
    let first = true;
    server.use(
      http.post("*/api/progression/v1/promo/redeem", ({ request }) => {
        keys.push(request.headers.get("idempotency-key"));
        if (first) {
          first = false;
          return HttpResponse.error();
        }
        return HttpResponse.json({
          reward_kind: "verses",
          amount: 25,
          item: null,
          repeated: false,
        });
      }),
    );
    renderWithProviders(<PromoBlock />);
    await type("welcome");
    await userEvent.click(screen.getByTestId("promo-submit"));
    expect(await screen.findByRole("alert")).toHaveTextContent("Нет связи");
    await userEvent.click(screen.getByTestId("promo-submit"));
    await screen.findByTestId("promo-result");
    expect(keys).toHaveLength(2);
    expect(keys[0]).toBeTruthy();
    expect(keys[1]).toBe(keys[0]);
  });

  it("5xx не сбрасывает ключ (сервер мог выполнить запрос), 4xx — сбрасывает", async () => {
    signInAsUser();
    const keys: (string | null)[] = [];
    const statuses = [503, 500, 404, 404];
    server.use(
      http.post("*/api/progression/v1/promo/redeem", ({ request }) => {
        keys.push(request.headers.get("idempotency-key"));
        const status = statuses[keys.length - 1]!;
        return HttpResponse.json({ code: "x", title: "x", status }, { status });
      }),
    );
    renderWithProviders(<PromoBlock />);
    await type("welcome");
    for (let i = 0; i < 4; i++) {
      await userEvent.click(screen.getByTestId("promo-submit"));
      await waitFor(() => expect(keys).toHaveLength(i + 1));
      await screen.findByRole("alert");
    }
    expect(keys[1]).toBe(keys[0]);
    expect(keys[2]).toBe(keys[0]);
    // После 404 (4xx) следующая попытка — с новым ключом.
    expect(keys[3]).not.toBe(keys[2]);
  });
});
