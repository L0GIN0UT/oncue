import { describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { server, setupMockBackend } from "@/test/msw";
import { renderWithProviders } from "@/test/render";
import { LoginForm, RegisterForm } from "./AuthForms";
import { ChangePasswordForm, ForgotPasswordForm } from "./PasswordForms";

const replace = vi.hoisted(() => vi.fn());
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace, push: vi.fn() }) }));

setupMockBackend();

const ID = "11111111-1111-4111-8111-111111111111";
const challenge = (purpose: string) => ({
  challenge_id: ID,
  code_length: 6,
  expires_in_s: 600,
  purpose,
  resend_after_s: 0,
});
const session = { expires_in: 900, user: { id: "u1", display_name: "Ян", roles: [], perms: [] } };
const problem = (status: number, code: string, details?: object) =>
  HttpResponse.json({ type: "about:blank", title: code, status, code, details }, { status });
const consents = [
  { kind: "terms", version: "1" },
  { kind: "privacy", version: "1" },
  { kind: "age_18", version: "1" },
];

/** Запоминает тела запросов к ручке и отвечает заданным ответом. */
function route(path: string, respond: () => Response) {
  const bodies: unknown[] = [];
  server.use(
    http.post(`*/api/identity/v1/auth/${path}`, async ({ request }) => {
      bodies.push(await request.json());
      return respond();
    }),
  );
  return bodies;
}

async function fillRegister(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByLabelText("Ник"), "Ян");
  await user.type(screen.getByLabelText("E-mail"), "yan@example.com");
  await user.type(screen.getByLabelText("Пароль"), "correct horse 1");
  for (const box of screen.getAllByRole("checkbox")) await user.click(box);
  await user.click(screen.getByRole("button", { name: "Зарегистрироваться" }));
}

describe("регистрация", () => {
  it("register → 202 → ввод кода → verify-email → сессия и переход на next", async () => {
    replace.mockClear();
    const reg = route("register", () => HttpResponse.json(challenge("register"), { status: 202 }));
    const verify = route("verify-email", () => HttpResponse.json(session));
    const user = userEvent.setup();
    renderWithProviders(<RegisterForm consents={consents} next="/online" />);
    await fillRegister(user);

    await user.type(await screen.findByTestId("code-input"), "123456");
    await user.click(screen.getByTestId("code-submit"));
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/online"));
    expect(reg).toHaveLength(1);
    expect(verify).toEqual([{ challenge_id: ID, code: "123456" }]);
  });

  it("неверный код остаётся на шаге ввода и показывает оставшиеся попытки", async () => {
    replace.mockClear();
    route("register", () => HttpResponse.json(challenge("register"), { status: 202 }));
    route("verify-email", () => problem(422, "auth.code_invalid", { attempts_left: 4 }));
    const user = userEvent.setup();
    renderWithProviders(<RegisterForm consents={consents} />);
    await fillRegister(user);
    await user.type(await screen.findByTestId("code-input"), "000000");
    await user.click(screen.getByTestId("code-submit"));
    expect(await screen.findByTestId("code-error")).toHaveTextContent("Осталось 4 попытки");
    expect(replace).not.toHaveBeenCalled();
  });
});

describe("вход", () => {
  async function fillLogin() {
    const user = userEvent.setup();
    renderWithProviders(<LoginForm next="/solo" />);
    await user.type(screen.getByLabelText("Почта или ник"), "yan");
    await user.type(screen.getByLabelText("Пароль"), "correct horse 1");
    await user.click(screen.getByRole("button", { name: "Войти" }));
    return user;
  }

  it("authenticated: сразу на next, шага кода нет", async () => {
    replace.mockClear();
    route("login", () => HttpResponse.json({ status: "authenticated", ...session }));
    await fillLogin();
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/solo"));
    expect(screen.queryByTestId("code-step")).not.toBeInTheDocument();
  });

  it("code_required: шаг кода → login/confirm → переход", async () => {
    replace.mockClear();
    route("login", () =>
      HttpResponse.json({ status: "code_required", challenge: challenge("login") }),
    );
    const confirm = route("login/confirm", () => HttpResponse.json(session));
    const user = await fillLogin();
    await user.type(await screen.findByTestId("code-input"), "654321");
    expect(replace).not.toHaveBeenCalled();
    await user.click(screen.getByTestId("code-submit"));
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/solo"));
    expect(confirm).toEqual([{ challenge_id: ID, code: "654321" }]);
  });

  it("неподтверждённый e-mail: код регистрации уходит в verify-email", async () => {
    replace.mockClear();
    route("login", () =>
      HttpResponse.json({ status: "code_required", challenge: challenge("register") }),
    );
    const verify = route("verify-email", () => HttpResponse.json(session));
    const confirm = route("login/confirm", () => HttpResponse.json(session));
    const user = await fillLogin();
    await user.type(await screen.findByTestId("code-input"), "654321");
    await user.click(screen.getByTestId("code-submit"));
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/solo"));
    expect(verify).toEqual([{ challenge_id: ID, code: "654321" }]);
    expect(confirm).toEqual([]);
  });

  it("есть ссылка «Забыл пароль»", () => {
    renderWithProviders(<LoginForm next={null} />);
    expect(screen.getByTestId("forgot-link")).toHaveAttribute("href", "/forgot-password");
  });
});

describe("забыл пароль", () => {
  async function start(email: string) {
    const user = userEvent.setup();
    const view = renderWithProviders(<ForgotPasswordForm />);
    await user.type(screen.getByLabelText("E-mail"), email);
    await user.click(screen.getByRole("button", { name: "Получить код" }));
    await screen.findByTestId("code-step");
    return { user, view };
  }

  it("сброс отправляет challenge_id, код и новый пароль", async () => {
    route("password/forgot", () => HttpResponse.json(challenge("reset"), { status: 202 }));
    const reset = route("password/reset", () => HttpResponse.json(session));
    replace.mockClear();
    const { user } = await start("yan@example.com");
    await user.type(screen.getByTestId("code-input"), "111111");
    await user.type(screen.getByLabelText("Новый пароль"), "brand new pass 9");
    await user.click(screen.getByTestId("code-submit"));
    // Сессия уже открыта сервером: сразу в приложение, без повторного входа и второго кода.
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/"));
    expect(reset).toEqual([{ challenge_id: ID, code: "111111", password: "brand new pass 9" }]);
  });

  it("экран кода одинаков для любого адреса и не намекает на существование аккаунта", async () => {
    route("password/forgot", () => HttpResponse.json(challenge("reset"), { status: 202 }));
    // Сгенерированные useId различаются между рендерами — на разметку они не влияют.
    const normalize = (html: string) => html.replace(/ (id|for|aria-describedby)="[^"]*"/g, "");
    const first = await start("exists@example.com");
    const html1 = normalize(screen.getByTestId("code-step").outerHTML);
    first.view.unmount();
    await start("nobody@example.org");
    const html2 = normalize(screen.getByTestId("code-step").outerHTML);
    expect(html2).toBe(html1);
    expect(html1).not.toMatch(/не найден|не существует|нет такого/i);
  });
});

describe("смена пароля в профиле", () => {
  it("current_password → код + новый пароль → confirm", async () => {
    const begin = route("password/change", () =>
      HttpResponse.json(challenge("password_change"), { status: 202 }),
    );
    const confirm = route("password/change/confirm", () => new HttpResponse(null, { status: 204 }));
    const user = userEvent.setup();
    renderWithProviders(<ChangePasswordForm />);
    await user.type(screen.getByLabelText("Текущий пароль"), "old password 1");
    await user.click(screen.getByRole("button", { name: "Получить код" }));
    await user.type(await screen.findByTestId("code-input"), "222222");
    await user.type(screen.getByLabelText("Новый пароль"), "brand new pass 9");
    await user.click(screen.getByTestId("code-submit"));
    expect(await screen.findByTestId("change-done")).toBeInTheDocument();
    expect(begin).toEqual([{ current_password: "old password 1" }]);
    expect(confirm).toEqual([
      { challenge_id: ID, code: "222222", new_password: "brand new pass 9" },
    ]);
  });

  it("неверный текущий пароль — ошибка, шага кода нет", async () => {
    route("password/change", () => problem(401, "auth.invalid_credentials"));
    const user = userEvent.setup();
    renderWithProviders(<ChangePasswordForm />);
    await user.type(screen.getByLabelText("Текущий пароль"), "nope");
    await user.click(screen.getByRole("button", { name: "Получить код" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Неверный логин или пароль");
    expect(screen.queryByTestId("code-step")).not.toBeInTheDocument();
  });
});
