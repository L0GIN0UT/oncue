import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { CosmeticCard } from "@/shared/api/types";
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
