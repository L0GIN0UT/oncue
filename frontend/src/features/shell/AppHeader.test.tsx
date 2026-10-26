import { describe, expect, it, vi } from "vitest";
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "@/test/render";
import type { HomePage } from "@/shared/api/types";
import { AppHeader } from "./AppHeader";
import { HeaderLeading, HeaderLeadingProvider } from "./header-leading";

let subject: { kind: "user" | "guest"; display_name: string | null } | null = null;
let home: HomePage | undefined;

vi.mock("next/navigation", () => ({ usePathname: () => "/" }));
vi.mock("@/features/auth/session", () => ({
  useSessionInfo: () => ({ data: { subject } }),
  useViewer: () => ({ kind: subject?.kind ?? "anonymous", pending: false }),
}));
vi.mock("@/features/daily/DailyCards", () => ({
  useHomePage: (_: unknown, opts?: { enabled?: boolean }) => ({
    data: opts?.enabled === false ? undefined : home,
  }),
}));
vi.mock("@/features/events/EventBanner", () => ({ HeaderEventLink: () => null }));
vi.mock("@/features/friends/use-presence", () => ({ PresenceHeartbeat: () => null }));
vi.mock("@/features/notifications/NotificationBell", () => ({
  NotificationBell: () => <a href="/notifications">Уведомления</a>,
}));

const card = (domain: "music" | "film", status: string) => ({
  mode_key: `${domain}.daily.classic`,
  domain,
  title_key: "t",
  day: "2026-10-26",
  status,
  max_score: 30,
  next_reset_at: "2026-10-27T00:00:00Z",
  session_id: null,
  score: null,
  presentation: "score",
});

function setHome(music: string, film: string, streak = 12) {
  home = {
    blocks: {
      modes: { status: "ok", data: [] },
      dailies: { status: "ok", data: [card("music", music), card("film", film)] },
      progress: {
        status: "ok",
        data: { level: 3, xp_in_level: 1, xp_to_next: 9, best_streak: streak },
      },
    },
  } as unknown as HomePage;
}

function renderHeader(leading?: React.ReactNode) {
  return renderWithProviders(
    <HeaderLeadingProvider>
      {leading}
      <AppHeader />
    </HeaderLeadingProvider>,
  );
}

describe("AppHeader", () => {
  it("знак по центру: левая и правая группы одной ширины (1fr auto 1fr)", () => {
    subject = null;
    renderHeader();
    const header = screen.getByRole("banner");
    expect(header.className).toContain("grid-cols-[1fr_auto_1fr]");
    expect(within(header).getByRole("link", { name: /Guessverse/ })).toHaveAttribute("href", "/");
  });

  it("гостю — кнопка «Войти», аватара и уведомлений нет", () => {
    subject = { kind: "guest", display_name: null };
    renderHeader();
    expect(screen.getByRole("link", { name: "Войти" })).toHaveAttribute("href", "/login");
    expect(screen.queryByTestId("user-name")).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Уведомления" })).not.toBeInTheDocument();
  });

  it("пользователю — аватар-ссылка в профиль с именем и колокольчик", () => {
    subject = { kind: "user", display_name: "Иван Логин" };
    renderHeader();
    expect(screen.getByTestId("user-name")).toHaveTextContent("Иван Логин");
    expect(screen.getByRole("link", { name: "Профиль" })).toHaveAttribute("href", "/profile");
    expect(screen.getByRole("link", { name: "Уведомления" })).toBeInTheDocument();
  });

  it("слот слева подменяется экраном процесса и возвращается при уходе", () => {
    subject = { kind: "user", display_name: "Иван" };
    const { rerender } = renderHeader(
      <HeaderLeading>
        <button>Сдаться</button>
      </HeaderLeading>,
    );
    expect(screen.getByRole("button", { name: "Сдаться" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Меню" })).not.toBeInTheDocument();
    rerender(
      <HeaderLeadingProvider>
        <AppHeader />
      </HeaderLeadingProvider>,
    );
    expect(screen.getByRole("button", { name: "Меню" })).toBeInTheDocument();
  });
});

describe("MenuDrawer", () => {
  it("открывается по кнопке: шесть пунктов, статусы дейликов и серия недели", async () => {
    subject = { kind: "user", display_name: "Иван" };
    setHome("in_progress", "not_started");
    renderHeader();
    await userEvent.click(screen.getByRole("button", { name: "Меню" }));
    const nav = screen.getByRole("navigation", { name: "Разделы" });
    expect(within(nav).getAllByRole("link")).toHaveLength(6);
    expect(within(nav).getByTestId("nav-today-music")).toHaveTextContent("продолжить");
    expect(within(nav).getByTestId("nav-today-film")).toHaveTextContent("не сыграно");
    expect(within(nav).getByTestId("nav-today-film")).toHaveAttribute("href", "/?domain=film");
    for (const id of ["nav-solo", "nav-online", "nav-collections", "nav-profile"])
      expect(within(nav).getByTestId(id)).toBeInTheDocument();
    expect(screen.getByTestId("menu-week")).toHaveTextContent("Серия: 12 дней");
    expect(within(screen.getByTestId("menu-week")).getAllByRole("listitem")).toHaveLength(7);
  });

  it("гостю профиль и серия не показываются", async () => {
    subject = { kind: "guest", display_name: null };
    setHome("not_started", "not_started");
    renderHeader();
    await userEvent.click(screen.getByRole("button", { name: "Меню" }));
    expect(screen.queryByTestId("nav-profile")).not.toBeInTheDocument();
    expect(screen.queryByTestId("menu-week")).not.toBeInTheDocument();
  });

  it("Escape и крестик закрывают меню, фокус возвращается на кнопку", async () => {
    subject = { kind: "user", display_name: "Иван" };
    setHome("not_started", "not_started");
    renderHeader();
    const button = screen.getByRole("button", { name: "Меню" });
    await userEvent.click(button);
    expect(screen.getByRole("dialog", { name: "Разделы" })).toBeInTheDocument();
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(button).toHaveFocus();
    await userEvent.click(button);
    await userEvent.click(screen.getByRole("button", { name: "Закрыть меню" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});
