import { describe, expect, it } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { delay, http, HttpResponse } from "msw";
import { server, setupMockBackend, signInAsGuest, signInAsUser, world } from "@/test/msw";
import { renderWithProviders } from "@/test/render";
import type { ProgressCard } from "@/shared/api/types";
import { PROGRESS, SECTORS } from "../../../e2e/mocks/progression";
import { ProfileView } from "./ProfileView";
import { XpBar } from "./XpBar";

setupMockBackend();

const bar = () => screen.getByRole("progressbar", { name: "Опыт до следующего уровня" });

describe("ProfileView", () => {
  it("пользователь видит все блоки из данных API", async () => {
    signInAsUser();
    renderWithProviders(<ProfileView />);

    expect(await screen.findByTestId("level-value")).toHaveTextContent("Уровень 7");
    expect(bar()).toHaveAttribute("aria-valuenow", "24");
    expect(bar()).toHaveAttribute("aria-valuetext", "120 из 500 опыта до уровня 8");
    expect(screen.getByTestId("streaks")).toHaveTextContent("3 подряд, рекорд 9");
    expect(screen.getByTestId("boost")).toHaveTextContent("×1,5");
    expect(screen.getByTestId("wallet-total")).toHaveTextContent("40 Версов");
    expect(screen.getByTestId("wallet-earned")).toHaveTextContent("40");
    const done = screen.getByTestId("quest-play_three");
    expect(done).toHaveAttribute("data-completed", "true");
    expect(done).toHaveTextContent("Сыграй три раунда");
    expect(done).toHaveTextContent("+50 опыта, +5 Версов");
    expect(screen.getByTestId("quest-streak_week")).toHaveTextContent("2 из 7");
    expect(screen.getByTestId("badge-first_win")).toHaveAttribute("data-unlocked", "true");
    expect(screen.getByTestId("badge-streak_7")).toHaveTextContent("Прогресс: 3 из 7");
    for (const s of SECTORS) {
      const sector = screen.getByTestId(`sector-${s.key}`);
      expect(sector).toHaveTextContent(s.title);
      expect(sector).toHaveTextContent(`${Math.round(s.chance * 1000) / 10}%`);
    }
  });

  it("5b: ник и «Своя аватарка» в плитке «Внешность»; блоки 5b недоступны — профиль цел", async () => {
    signInAsUser();
    const view = renderWithProviders(<ProfileView />);
    expect(await screen.findByTestId("player-nickname")).toHaveTextContent("player");
    expect(await screen.findByTestId("avatar-status")).toHaveAttribute("data-status", "none");
    expect(screen.getByTestId("nav-shop")).toHaveAttribute("href", "/shop");
    view.unmount();

    world.scenario = { appearanceDown: true };
    renderWithProviders(<ProfileView />);
    expect(await screen.findByTestId("level-value")).toBeInTheDocument();
    expect(await screen.findByTestId("block-avatar-unavailable")).toBeInTheDocument();
    expect(screen.getByTestId("player-nickname")).toBeInTheDocument();
  });

  it("обзор: четыре плитки, календарь и баланс Версов в ссылке на магазин", async () => {
    server.use(
      http.get("*/api/web/v1/pages/online", () => HttpResponse.json({ status: "ok", blocks: {} })),
    );
    signInAsUser();
    renderWithProviders(<ProfileView />);

    const tiles = await screen.findByTestId("profile-tiles");
    expect(within(tiles).getByText("дней серия")).toBeInTheDocument();
    expect(within(tiles).getByText("всего опыта")).toBeInTheDocument();
    expect(within(tiles).getByText("заданий сегодня")).toBeInTheDocument();
    expect(await screen.findByTestId("activity-calendar")).toBeInTheDocument();
    expect(screen.getByTestId("nav-shop")).toHaveAttribute("href", "/shop");
    expect(screen.getByTestId("nav-shop")).toHaveTextContent("40 Версов");
  });

  it("«Лидеры» ведут к таблице лидеров, «Добавить» — к разделу друзей", async () => {
    server.use(
      http.get("*/api/web/v1/pages/online", () => HttpResponse.json({ status: "ok", blocks: {} })),
    );
    signInAsUser();
    renderWithProviders(<ProfileView />);

    expect(await screen.findByTestId("nav-leaderboards")).toHaveAttribute("href", "/leaderboards");
    expect(await screen.findByTestId("nav-friends")).toHaveAttribute("href", "/friends");
  });

  it("аноним и гость — приглашение вместо профиля", async () => {
    renderWithProviders(<ProfileView />);
    expect(await screen.findByTestId("profile-gate")).toHaveTextContent("Профиль — для игроков");
    expect(screen.getByRole("link", { name: "Войти" })).toHaveAttribute("href", "/login");
  });

  it("гость получает ссылку на регистрацию", async () => {
    signInAsGuest();
    renderWithProviders(<ProfileView />);
    expect(await screen.findByRole("link", { name: "Зарегистрироваться" })).toHaveAttribute(
      "href",
      "/register",
    );
  });

  it("401/403 от bff на запрос профиля — приглашение, а не ошибка", async () => {
    signInAsUser();
    server.use(
      http.get("*/api/web/v1/pages/profile", () =>
        HttpResponse.json(
          { code: "auth.guest_forbidden", title: "x", status: 403 },
          { status: 403 },
        ),
      ),
    );
    renderWithProviders(<ProfileView />);
    expect(await screen.findByRole("link", { name: "Зарегистрироваться" })).toBeInTheDocument();
  });

  it("погасший блок не роняет страницу", async () => {
    signInAsUser();
    world.scenario.progressDown = ["wallet", "badges"];
    renderWithProviders(<ProfileView />);

    expect(await screen.findByTestId("level-value")).toBeInTheDocument();
    expect(screen.getByTestId("block-wallet-unavailable")).toHaveTextContent("Появится позже");
    expect(screen.getByTestId("block-badges-unavailable")).toBeInTheDocument();
    expect(screen.getByTestId("quest-win_duel")).toBeInTheDocument();
  });

  it("сбой самой страницы — понятное сообщение", async () => {
    signInAsUser();
    server.use(http.get("*/api/web/v1/pages/profile", () => HttpResponse.error()));
    renderWithProviders(<ProfileView />);
    expect(await screen.findByTestId("profile-unavailable")).toBeInTheDocument();
  });
});

describe("колесо", () => {
  it("спин показывает результат без перезагрузки и блокирует кнопку", async () => {
    const user = userEvent.setup();
    signInAsUser();
    renderWithProviders(<ProfileView />);

    await user.click(await screen.findByTestId("wheel-spin"));

    expect(await screen.findByTestId("wheel-result")).toHaveTextContent("Выпало: 5 Версов");
    expect(screen.getByTestId("sector-verses_5")).toHaveAttribute("data-hit", "true");
    await waitFor(() => expect(screen.getByTestId("wheel-spin")).toBeDisabled());
    expect(await screen.findByTestId("wheel-spun")).toHaveTextContent("уже крутили");
    expect(world.progression.spinCalls).toBe(1);
  });

  it("уже крутил сегодня — кнопка выключена, результат из данных", async () => {
    signInAsUser();
    world.progression.spun.set("user.00000000-0000-4000-8000-000000000901", {
      day: "2026-10-23",
      sector_key: "empty",
      prize: { kind: "none", amount: 0 },
      spun_at: "2026-10-23T08:00:00Z",
      repeated: false,
    });
    renderWithProviders(<ProfileView />);

    expect(await screen.findByTestId("wheel-spin")).toBeDisabled();
    expect(screen.getByTestId("wheel-result")).toHaveTextContent("Выпало: Пусто");
  });

  it("repeated=true — сообщение «уже крутили» и прежний результат", async () => {
    const user = userEvent.setup();
    signInAsUser();
    world.scenario.spunElsewhere = true;
    renderWithProviders(<ProfileView />);

    await user.click(await screen.findByTestId("wheel-spin"));

    const result = await screen.findByTestId("wheel-result");
    expect(result).toHaveTextContent("Сегодня вы уже крутили");
    expect(result).toHaveTextContent("Выпало: 5 Версов");
  });

  it("409 — сообщение, кнопка остаётся доступной", async () => {
    const user = userEvent.setup();
    signInAsUser();
    world.scenario.spinConflict = true;
    renderWithProviders(<ProfileView />);

    await user.click(await screen.findByTestId("wheel-spin"));

    expect(await screen.findByTestId("wheel-error")).toHaveTextContent("недоступно");
    expect(screen.getByTestId("wheel-spin")).toBeEnabled();
    expect(screen.queryByTestId("wheel-result")).toBeNull();
  });

  it("обрыв сети — повтор безопасен и проходит", async () => {
    const user = userEvent.setup();
    signInAsUser();
    world.scenario.spinNetworkOnce = true;
    renderWithProviders(<ProfileView />);

    await user.click(await screen.findByTestId("wheel-spin"));
    expect(await screen.findByTestId("wheel-error")).toHaveTextContent("Нет связи");

    await user.click(screen.getByTestId("wheel-spin"));
    expect(await screen.findByTestId("wheel-result")).toHaveTextContent("Выпало");
    expect(screen.queryByTestId("wheel-error")).toBeNull();
  });

  it("во время спина кнопка заблокирована и помечена занятой", async () => {
    const user = userEvent.setup();
    signInAsUser();
    server.use(
      http.post("*/api/progression/v1/wheel/spin", async () => {
        await delay(120);
        return HttpResponse.json({
          day: "2026-10-23",
          sector_key: "empty",
          prize: { kind: "none", amount: 0, multiplier: null, matches: null, item_key: null },
          spun_at: "2026-10-23T08:00:00Z",
          repeated: false,
        });
      }),
    );
    renderWithProviders(<ProfileView />);

    await user.click(await screen.findByTestId("wheel-spin"));
    const button = screen.getByTestId("wheel-spin");
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute("aria-busy", "true");
    expect(await screen.findByTestId("wheel-result")).toHaveTextContent("Выпало: Пусто");
  });

  it("колесо выключено — без кнопки", async () => {
    signInAsUser();
    server.use(
      http.get("*/api/web/v1/pages/profile", () =>
        HttpResponse.json({
          blocks: {
            progress: { status: "unavailable", data: null },
            wallet: { status: "unavailable", data: null },
            quests: { status: "unavailable", data: null },
            badges: { status: "unavailable", data: null },
            wheel: {
              status: "ok",
              data: { enabled: false, can_spin: false, next_spin_at: null, sectors: [] },
            },
          },
        }),
      ),
    );
    renderWithProviders(<ProfileView />);
    expect(await screen.findByTestId("wheel-disabled")).toBeInTheDocument();
    expect(screen.queryByTestId("wheel-spin")).toBeNull();
  });
});

const prize = (kind: "verses" | "none", amount: number) => ({
  kind,
  amount,
  multiplier: null,
  matches: null,
  item_key: null,
});

function profileWith(wheel: object) {
  const down = { status: "unavailable", data: null };
  return HttpResponse.json({
    blocks: { progress: down, wallet: down, quests: down, badges: down, wheel },
  });
}

describe("колесо: ошибки доступа и защита от повторов", () => {
  it("401 при спине — сообщение со ссылкой на вход", async () => {
    const user = userEvent.setup();
    signInAsUser();
    server.use(
      http.post("*/api/progression/v1/wheel/spin", () =>
        HttpResponse.json(
          { code: "auth.unauthenticated", title: "x", status: 401 },
          { status: 401 },
        ),
      ),
    );
    renderWithProviders(<ProfileView />);

    await user.click(await screen.findByTestId("wheel-spin"));

    const error = await screen.findByTestId("wheel-error");
    expect(error).toHaveTextContent("Сессия закончилась");
    expect(within(error).getByRole("link", { name: "Войти" })).toHaveAttribute("href", "/login");
  });

  it("403 при спине — отдельный текст про аккаунт", async () => {
    const user = userEvent.setup();
    signInAsUser();
    server.use(
      http.post("*/api/progression/v1/wheel/spin", () =>
        HttpResponse.json(
          { code: "auth.guest_forbidden", title: "x", status: 403 },
          { status: 403 },
        ),
      ),
    );
    renderWithProviders(<ProfileView />);

    await user.click(await screen.findByTestId("wheel-spin"));

    const error = await screen.findByTestId("wheel-error");
    expect(error).toHaveTextContent("только игрокам с аккаунтом");
    expect(error).not.toHaveTextContent("Не получилось");
  });

  it("двойной клик отправляет один запрос", async () => {
    const user = userEvent.setup();
    signInAsUser();
    renderWithProviders(<ProfileView />);
    const button = await screen.findByTestId("wheel-spin");

    await user.dblClick(button);

    await screen.findByTestId("wheel-result");
    expect(world.progression.spinCalls).toBe(1);
  });

  it("если обновить профиль не удалось, кнопка всё равно заблокирована результатом", async () => {
    const user = userEvent.setup();
    signInAsUser();
    renderWithProviders(<ProfileView />);
    const button = await screen.findByTestId("wheel-spin");
    server.use(http.get("*/api/web/v1/pages/profile", () => HttpResponse.error()));

    await user.click(button);

    expect(await screen.findByTestId("wheel-result")).toBeInTheDocument();
    expect(screen.getByTestId("wheel-spin")).toBeDisabled();
  });

  it("сектор и приз показываются так, как пришли в API (шанс 12,5%, 7 Версов)", async () => {
    const user = userEvent.setup();
    signInAsUser();
    server.use(
      http.get("*/api/web/v1/pages/profile", () =>
        profileWith({
          status: "ok",
          data: {
            enabled: true,
            can_spin: true,
            next_spin_at: null,
            today: null,
            sectors: [{ key: "seven", title: "Семь!", chance: 0.125, prize: prize("verses", 7) }],
          },
        }),
      ),
      http.post("*/api/progression/v1/wheel/spin", () =>
        HttpResponse.json({
          day: "2026-10-23",
          sector_key: "unknown_sector",
          prize: prize("verses", 7),
          spun_at: "2026-10-23T08:00:00Z",
          repeated: false,
        }),
      ),
    );
    renderWithProviders(<ProfileView />);

    expect(await screen.findByTestId("sector-seven")).toHaveTextContent("Семь!");
    expect(screen.getByTestId("sector-seven")).toHaveTextContent("12,5%");
    await user.click(screen.getByTestId("wheel-spin"));
    expect(await screen.findByTestId("wheel-result")).toHaveTextContent("Выпало: 7 Версов");
  });
});

describe("доступ к профилю", () => {
  it("403 аккаунта с чужим кодом — нейтральный отказ, не ссылка на регистрацию", async () => {
    signInAsUser();
    server.use(
      http.get("*/api/web/v1/pages/profile", () =>
        HttpResponse.json({ code: "auth.forbidden", title: "x", status: 403 }, { status: 403 }),
      ),
    );
    renderWithProviders(<ProfileView />);

    expect(await screen.findByTestId("profile-gate")).toHaveTextContent("Нет доступа");
    expect(screen.queryByRole("link", { name: "Зарегистрироваться" })).toBeNull();
  });
});

describe("XpBar", () => {
  const card = (p: Partial<ProgressCard>): ProgressCard => ({ ...PROGRESS, ...p });
  const render = (p: Partial<ProgressCard>) => {
    const c = card(p);
    return renderWithProviders(
      <XpBar xpInLevel={c.xp_in_level} xpToNext={c.xp_to_next} level={c.level} />,
    );
  };

  it("начало уровня — пустая полоса", () => {
    render({ xp_in_level: 0, xp_to_next: 500 });
    expect(bar()).toHaveAttribute("aria-valuenow", "0");
    expect(within(bar()).getByRole("generic", { hidden: true })).toHaveStyle({ width: "0%" });
  });

  it("за 1 опыт до уровня — не 100%", () => {
    render({ xp_in_level: 499, xp_to_next: 1 });
    expect(bar()).toHaveAttribute("aria-valuenow", "99");
  });

  it("максимальный уровень — полная полоса и свой текст", () => {
    render({ level: 100, xp_in_level: 1_000, xp_to_next: null });
    expect(bar()).toHaveAttribute("aria-valuenow", "100");
    expect(bar()).toHaveAttribute("aria-valuetext", "Достигнут максимальный уровень");
  });
});
