import { afterEach, describe, expect, it, vi } from "vitest";
import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { server, setupMockBackend } from "@/test/msw";
import { renderWithProviders } from "@/test/render";
import { unwrap, api } from "@/shared/api/client";
import { CodeStep, type Challenge } from "./CodeStep";

setupMockBackend();
afterEach(() => vi.useRealTimers());

const ID = "11111111-1111-4111-8111-111111111111";
const challenge = (over: Partial<Challenge> = {}): Challenge => ({
  challenge_id: ID,
  code_length: 6,
  expires_in_s: 600,
  purpose: "register",
  resend_after_s: 0,
  ...over,
});
const problem = (status: number, code: string, details?: object, headers?: HeadersInit) =>
  HttpResponse.json(
    { type: "about:blank", title: code, status, code, details: details ?? null },
    { status, headers },
  );

async function setup(
  onSubmit: (code: string) => Promise<void> = async () => {},
  c: Challenge = challenge(),
  userOpts: Parameters<typeof userEvent.setup>[0] = {},
) {
  const user = userEvent.setup(userOpts);
  renderWithProviders(<CodeStep challenge={c} submitLabel="Подтвердить" onSubmit={onSubmit} />);
  return user;
}

describe("CodeStep", () => {
  it("поле кода: цифры, one-time-code, длина из ответа; успешная отправка", async () => {
    const onSubmit = vi.fn(async () => {});
    const user = await setup(onSubmit, challenge({ code_length: 4 }));
    const input = screen.getByTestId("code-input");
    expect(input).toHaveAttribute("inputmode", "numeric");
    expect(input).toHaveAttribute("autocomplete", "one-time-code");
    await user.type(input, "1a2b3 4 5");
    expect(input).toHaveValue("1234");
    await user.click(screen.getByTestId("code-submit"));
    expect(onSubmit).toHaveBeenCalledWith(
      "1234",
      expect.objectContaining({ challenge_id: ID }),
      expect.any(FormData),
    );
  });

  it("неполный код не уходит на сервер", async () => {
    const onSubmit = vi.fn(async () => {});
    const user = await setup(onSubmit);
    await user.type(screen.getByTestId("code-input"), "123");
    await user.click(screen.getByTestId("code-submit"));
    expect(onSubmit).not.toHaveBeenCalled();
    expect(screen.getByTestId("code-error")).toHaveTextContent("Неверный код");
  });

  async function failWith(res: () => Response) {
    server.use(http.post("*/api/identity/v1/auth/verify-email", () => res()));
    const user = await setup(async (code) => {
      unwrap(
        await api().identity.POST("/v1/auth/verify-email", {
          body: { challenge_id: ID, code },
        }),
      );
    });
    await user.type(screen.getByTestId("code-input"), "123456");
    await user.click(screen.getByTestId("code-submit"));
    return user;
  }

  it("неверный код: счётчик оставшихся попыток, поле очищено, ошибка в aria-live", async () => {
    await failWith(() => problem(422, "auth.code_invalid", { attempts_left: 2 }));
    const err = await screen.findByTestId("code-error");
    expect(err).toHaveTextContent("Неверный код. Осталось 2 попытки.");
    expect(err.closest("[aria-live]")).toHaveAttribute("aria-live", "assertive");
    expect(screen.getByTestId("code-input")).toHaveValue("");
  });

  it("истёкший код: просит запросить новый", async () => {
    await failWith(() => problem(410, "auth.code_expired"));
    expect(await screen.findByTestId("code-error")).toHaveTextContent("Код истёк");
  });

  it("лимит попыток: ввод и отправка заблокированы", async () => {
    await failWith(() => problem(429, "auth.code_limit_exceeded"));
    expect(await screen.findByTestId("code-error")).toHaveAttribute("data-kind", "limit");
    expect(screen.getByTestId("code-input")).toBeDisabled();
    expect(screen.getByTestId("code-submit")).toBeDisabled();
  });

  it("429 с Retry-After: кнопка отправки ждёт указанное время", async () => {
    await failWith(() => problem(429, "http.429", undefined, { "Retry-After": "7" }));
    expect(await screen.findByTestId("code-error")).toHaveTextContent("Повторите через 7 с");
    expect(screen.getByTestId("code-submit")).toBeDisabled();
    expect(screen.getByTestId("code-submit")).toHaveTextContent("Повторить через 7 с");
  });

  it("сетевая ошибка не раскрывает ничего лишнего", async () => {
    await failWith(() => HttpResponse.error());
    expect(await screen.findByTestId("code-error")).toHaveTextContent("Нет связи");
  });

  it("повторная отправка: таймер resend_after_s, затем новый код заменяет прежний", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval", "Date"] });
    const requests: unknown[] = [];
    server.use(
      http.post("*/api/identity/v1/auth/codes/resend", async ({ request }) => {
        requests.push(await request.json());
        return HttpResponse.json(
          {
            ...challenge({ resend_after_s: 30 }),
            challenge_id: "22222222-2222-4222-8222-222222222222",
          },
          { status: 202 },
        );
      }),
    );
    const user = await setup(undefined, challenge({ resend_after_s: 3 }), {
      advanceTimers: vi.advanceTimersByTime,
    });
    const resend = screen.getByTestId("code-resend");
    expect(resend).toBeDisabled();
    expect(resend).toHaveTextContent("через 3 с");
    act(() => void vi.advanceTimersByTime(2000));
    expect(resend).toHaveTextContent("через 1 с");
    act(() => void vi.advanceTimersByTime(1000));
    expect(resend).toBeEnabled();

    await user.type(screen.getByTestId("code-input"), "123");
    await user.click(resend);
    await waitFor(() => expect(requests).toEqual([{ challenge_id: ID }]));
    await screen.findByText(/Новый код отправлен/);
    expect(screen.getByTestId("code-input")).toHaveValue("");
    expect(screen.getByTestId("code-resend")).toBeDisabled();
    expect(screen.getByTestId("code-resend")).toHaveTextContent("через 30 с");
  });

  it("слишком ранний повтор: кнопка снова ждёт Retry-After", async () => {
    server.use(
      http.post("*/api/identity/v1/auth/codes/resend", () =>
        problem(429, "auth.code_resend_too_early", undefined, { "Retry-After": "12" }),
      ),
    );
    const user = await setup();
    await user.click(screen.getByTestId("code-resend"));
    expect(await screen.findByTestId("code-error")).toHaveTextContent("Повторите через 12 с");
    expect(screen.getByTestId("code-resend")).toBeDisabled();
  });
});
