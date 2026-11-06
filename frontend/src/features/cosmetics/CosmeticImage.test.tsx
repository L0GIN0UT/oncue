import { render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { CosmeticCard } from "@/shared/api/types";
import { renderWithProviders } from "@/test/render";
import { CosmeticImage, safeImageUrl } from "./CosmeticImage";

function mockReducedMotion(reduce: boolean) {
  vi.stubGlobal(
    "matchMedia",
    vi.fn((query: string) => ({
      matches: reduce && query.includes("prefers-reduced-motion"),
      media: query,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })),
  );
}

const base: CosmeticCard = {
  id: "i1",
  key: "frame_gold",
  slot: "frame",
  title: "Золотая рамка",
  rarity: "rare",
  media_kind: "static",
  asset_url: "/cosmetics/frame_gold.webp",
  poster_url: null,
  grants_custom_avatar: false,
};

afterEach(() => vi.unstubAllGlobals());

describe("CosmeticImage", () => {
  it("static: картинка с alt из названия и ленивой загрузкой", () => {
    mockReducedMotion(false);
    render(<CosmeticImage item={base} />);
    const img = screen.getByRole("img", { name: "Золотая рамка" });
    expect(img).toHaveAttribute("src", "/cosmetics/frame_gold.webp");
    expect(img).toHaveAttribute("loading", "lazy");
  });

  it("animated без reduced-motion: анимированный asset_url", () => {
    mockReducedMotion(false);
    render(
      <CosmeticImage item={{ ...base, media_kind: "animated", poster_url: "/cosmetics/p.webp" }} />,
    );
    expect(screen.getByRole("img")).toHaveAttribute("src", "/cosmetics/frame_gold.webp");
  });

  it("animated + reduced-motion: постер", () => {
    mockReducedMotion(true);
    render(
      <CosmeticImage item={{ ...base, media_kind: "animated", poster_url: "/cosmetics/p.webp" }} />,
    );
    expect(screen.getByRole("img")).toHaveAttribute("src", "/cosmetics/p.webp");
  });

  it("animated + reduced-motion без постера: статичная заглушка, анимация не грузится", () => {
    mockReducedMotion(true);
    const { container } = render(<CosmeticImage item={{ ...base, media_kind: "animated" }} />);
    expect(container.querySelector("img")).toBeNull();
    expect(screen.getByRole("img", { name: "Золотая рамка" })).toHaveAttribute(
      "data-placeholder",
      "true",
    );
  });

  it("static + reduced-motion остаётся картинкой", () => {
    mockReducedMotion(true);
    render(<CosmeticImage item={base} />);
    expect(screen.getByRole("img")).toHaveAttribute("src", "/cosmetics/frame_gold.webp");
  });

  it("небезопасный адрес (javascript:, чужой протокол) не попадает в src", () => {
    mockReducedMotion(false);
    const { container } = render(
      <CosmeticImage item={{ ...base, asset_url: "javascript:alert(1)" }} />,
    );
    expect(container.querySelector("img")).toBeNull();
    expect(screen.getByRole("img")).toHaveAttribute("data-placeholder", "true");
  });

  it("без window.matchMedia (jsdom) не падает", () => {
    render(<CosmeticImage item={base} />);
    expect(screen.getByRole("img")).toBeInTheDocument();
  });
});

describe("CosmeticImage: заглушка без картинки", () => {
  const noImage = { ...base, asset_url: "" };

  it.each([
    ["avatar", "avatar"],
    ["frame", "frame"],
    ["background", "background"],
  ])("слот %s: своя иконка, не пустой квадрат", (slot, kind) => {
    mockReducedMotion(false);
    render(<CosmeticImage item={{ ...noImage, slot }} />);
    const placeholder = screen.getByRole("img", { name: "Золотая рамка" });
    expect(placeholder).toHaveAttribute("data-placeholder-kind", kind);
    expect(placeholder.querySelector("svg")).not.toBeNull();
  });

  it("иконки разных слотов различаются", () => {
    mockReducedMotion(false);
    const icon = (slot: string) => {
      const { container, unmount } = render(
        <CosmeticImage item={{ ...noImage, slot }} decorative />,
      );
      const html = container.querySelector("svg")?.getAttribute("class") ?? "";
      unmount();
      return html;
    };
    expect(new Set([icon("avatar"), icon("frame"), icon("background")]).size).toBe(3);
  });

  it("слот, которого нет в списке (из конфига): общая иконка, экран не ломается", () => {
    mockReducedMotion(false);
    render(<CosmeticImage item={{ ...noImage, slot: "season_banner" }} />);
    const placeholder = screen.getByRole("img", { name: "Золотая рамка" });
    expect(placeholder).toHaveAttribute("data-placeholder-kind", "other");
    expect(placeholder.querySelector("svg")).not.toBeNull();
  });

  it("декоративная заглушка скрыта от скринридера, у карточки есть доступное имя", () => {
    mockReducedMotion(false);
    const { container, unmount } = render(
      <CosmeticImage item={{ ...noImage, slot: "avatar" }} decorative />,
    );
    const hidden = container.querySelector("[data-placeholder]")!;
    expect(hidden).toHaveAttribute("aria-hidden", "true");
    expect(hidden).not.toHaveAttribute("aria-label");
    expect(screen.queryByRole("img")).toBeNull();
    unmount();
    render(<CosmeticImage item={{ ...noImage, slot: "avatar" }} />);
    expect(screen.getByRole("img", { name: "Золотая рамка" })).toBeInTheDocument();
  });

  it("«Своя аватарка»: иконка загрузки и подпись «Загрузите свою», это не сломанная картинка", () => {
    mockReducedMotion(false);
    renderWithProviders(
      <CosmeticImage item={{ ...noImage, slot: "avatar", grants_custom_avatar: true }} showLabel />,
    );
    const placeholder = screen.getByRole("img", { name: "Золотая рамка" });
    expect(placeholder).toHaveAttribute("data-placeholder-kind", "custom_avatar");
    expect(within(placeholder).getByText("Загрузите свою")).toBeInTheDocument();
  });

  it("«Своя аватарка» без подписи (мелкая плитка): только иконка загрузки", () => {
    mockReducedMotion(false);
    renderWithProviders(
      <CosmeticImage item={{ ...noImage, slot: "avatar", grants_custom_avatar: true }} />,
    );
    const placeholder = screen.getByRole("img", { name: "Золотая рамка" });
    expect(placeholder).toHaveAttribute("data-placeholder-kind", "custom_avatar");
    expect(screen.queryByText("Загрузите свою")).not.toBeInTheDocument();
  });

  it("animated + reduced-motion без постера: та же заглушка слота", () => {
    mockReducedMotion(true);
    render(<CosmeticImage item={{ ...base, slot: "frame", media_kind: "animated" }} />);
    const placeholder = screen.getByRole("img", { name: "Золотая рамка" });
    expect(placeholder).toHaveAttribute("data-placeholder-kind", "frame");
    expect(placeholder.querySelector("svg")).not.toBeNull();
  });

  it("есть картинка: заглушки нет (ни у слота, ни у «Своей аватарки»)", () => {
    mockReducedMotion(false);
    const { container } = renderWithProviders(
      <CosmeticImage item={{ ...base, slot: "avatar", grants_custom_avatar: true }} showLabel />,
    );
    expect(container.querySelector("[data-placeholder]")).toBeNull();
    expect(container.querySelector("img")).not.toBeNull();
    expect(screen.queryByText("Загрузите свою")).not.toBeInTheDocument();
  });
});

describe("safeImageUrl", () => {
  it.each(["/cosmetics/fox.webp", "/ugc/avatars/a1.webp"])("пропускает %s", (url) => {
    expect(safeImageUrl(url)).toBe(url);
  });

  it.each([
    "/\\evil.com",
    "/\\evil.com/x.png",
    "//evil.com/cosmetics/x.png",
    "/api/progression/v1/x",
    "/media/a/x.png",
    "/cosmetics/../api/x",
    "/cosmetics/a\\b.png",
    "/cosmetics/a\nb.png",
    "/cosmetics/a\u0000.png",
    "https://evil.com/cosmetics/x.png",
    "javascript:alert(1)",
    "data:image/png;base64,AAAA",
    "blob:http://localhost/abc",
    "",
    null,
    undefined,
  ])("отбрасывает %j", (url) => {
    expect(safeImageUrl(url as string | null | undefined)).toBeNull();
  });

  it("blob: только с явным разрешением (превью аватарки)", () => {
    expect(safeImageUrl("blob:http://localhost/abc", { allowBlob: true })).toBe(
      "blob:http://localhost/abc",
    );
  });
});
