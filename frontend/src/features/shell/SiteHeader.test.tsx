import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, within } from "@testing-library/react";
import { renderWithProviders } from "@/test/render";
import { SiteChrome } from "./SiteChrome";
import { GameShell } from "./GameShell";
import { HeaderLeading, HeaderLeadingProvider } from "./header-leading";
import { SiteHeader } from "./SiteHeader";

let subject: { kind: "user" | "guest"; display_name: string | null } | null = null;
let pathname = "/";

vi.mock("next/navigation", () => ({ usePathname: () => pathname }));
vi.mock("@/features/auth/session", () => ({
  useSessionInfo: () => ({ data: { subject } }),
}));
vi.mock("@/features/friends/use-presence", () => ({
  PresenceHeartbeat: () => <i data-testid="presence" />,
}));
vi.mock("@/features/notifications/NotificationBell", () => ({
  NotificationBell: () => <a href="/notifications">Уведомления</a>,
}));
vi.mock("@/features/progression/api", () => ({
  profileQueryKey: ["page", "profile"],
  fetchProfilePage: async () => ({
    blocks: { wallet: { status: "ok", data: { total: 1250, earned: 1250, paid: 0 } } },
  }),
}));

beforeEach(() => {
  subject = { kind: "user", display_name: "L0GIN0UT" };
  pathname = "/";
});

describe("SiteHeader", () => {
  it("навигация «Главная / Соло / Онлайн / Сборники» со ссылками", () => {
    renderWithProviders(<SiteHeader />);
    const nav = screen.getByRole("navigation", { name: "Разделы" });
    const links = within(nav).getAllByRole("link");
    expect(links.map((l) => l.textContent)).toEqual(["Главная", "Соло", "Онлайн", "Сборники"]);
    expect(links.map((l) => l.getAttribute("href"))).toEqual([
      "/",
      "/solo",
      "/online",
      "/collections",
    ]);
  });

  it("активный пункт отмечен aria-current и янтарный, остальные нет", () => {
    pathname = "/solo";
    renderWithProviders(<SiteHeader />);
    expect(screen.getByTestId("nav-solo")).toHaveAttribute("aria-current", "page");
    expect(screen.getByTestId("nav-solo").className).toContain("text-music");
    expect(screen.getByTestId("nav-home")).not.toHaveAttribute("aria-current");
  });

  it("на странице вне разделов (профиль) активного пункта нет", () => {
    pathname = "/profile";
    renderWithProviders(<SiteHeader />);
    expect(document.querySelector("[aria-current]")).toBeNull();
  });

  it("пользователь: колокольчик, баланс Версов, лидеры, профиль с ником; меню-гамбургера нет", async () => {
    renderWithProviders(<SiteHeader />);
    expect(screen.getByRole("link", { name: "Уведомления" })).toBeInTheDocument();
    expect(await screen.findByRole("link", { name: /1\s250 Версов/ })).toHaveAttribute(
      "href",
      "/shop",
    );
    expect(screen.getByRole("link", { name: "Лидеры" })).toHaveAttribute("href", "/leaderboards");
    expect(screen.getByTestId("nav-profile")).toHaveAttribute("href", "/profile");
    expect(screen.getByTestId("user-name")).toHaveTextContent("L0GIN0UT");
    expect(screen.queryByTestId("menu-button")).toBeNull();
  });

  it("гость: только «Войти», без баланса и профиля", () => {
    subject = { kind: "guest", display_name: null };
    renderWithProviders(<SiteHeader />);
    expect(screen.getByRole("link", { name: "Войти" })).toHaveAttribute("href", "/login");
    expect(screen.queryByTestId("nav-verses")).toBeNull();
    expect(screen.queryByTestId("nav-profile")).toBeNull();
  });
});

describe("SiteChrome", () => {
  it.each(["/play/music.daily.classic", "/match/42", "/room/K7MQ2XWP"])(
    "%s — оболочка игры, шапки сайта нет",
    (p) => {
      pathname = p;
      renderWithProviders(
        <HeaderLeadingProvider>
          <SiteChrome />
        </HeaderLeadingProvider>,
      );
      expect(screen.getByTestId("game-shell")).toBeInTheDocument();
      expect(screen.queryByRole("navigation", { name: "Разделы" })).toBeNull();
    },
  );

  it("обычная страница — шапка сайта; присутствие работает в обоих случаях", () => {
    renderWithProviders(
      <HeaderLeadingProvider>
        <SiteChrome />
      </HeaderLeadingProvider>,
    );
    expect(screen.getByRole("navigation", { name: "Разделы" })).toBeInTheDocument();
    expect(screen.queryByTestId("game-shell")).toBeNull();
    expect(screen.getByTestId("presence")).toBeInTheDocument();
  });
});

describe("GameShell", () => {
  it("слоты: выход и громкость слева, режим под знаком, счёт справа", () => {
    renderWithProviders(
      <HeaderLeadingProvider>
        <GameShell
          exit={<button>Выйти</button>}
          volume={<button>Громкость</button>}
          modeLabel="Дейлик · музыка"
          score="0 / 30"
        />
      </HeaderLeadingProvider>,
    );
    expect(screen.getByRole("button", { name: "Выйти" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Громкость" })).toBeInTheDocument();
    expect(screen.getByTestId("game-mode")).toHaveTextContent("Дейлик · музыка");
    expect(screen.getByText("0 / 30")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /на главную/ })).toHaveAttribute("href", "/");
  });

  it("без exit слева кнопка из слота HeaderLeading (действующие экраны не теряют выход)", () => {
    renderWithProviders(
      <HeaderLeadingProvider>
        <HeaderLeading>
          <button>Сдаться</button>
        </HeaderLeading>
        <GameShell />
      </HeaderLeadingProvider>,
    );
    expect(screen.getByRole("button", { name: "Сдаться" })).toBeInTheDocument();
  });
});
