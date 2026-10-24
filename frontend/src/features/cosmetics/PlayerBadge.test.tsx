import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { PlayerBadge, ProfileBackdrop } from "./PlayerBadge";
import type { AppearanceLike, CosmeticLike } from "./slots";

const item = (slot: string, key: string, title = key): CosmeticLike => ({
  key,
  slot,
  title,
  media_kind: "static",
  asset_url: `/cosmetics/${key}.webp`,
  poster_url: null,
});

const full: AppearanceLike = {
  custom_avatar_url: null,
  slots: [
    { slot: "avatar", item: item("avatar", "fox", "Лис") },
    { slot: "frame", item: item("frame", "gold", "Золотая рамка") },
    { slot: "title", item: item("title", "champ", "Чемпион") },
    { slot: "background", item: item("background", "stars", "Звёзды") },
  ],
};

describe("PlayerBadge", () => {
  it("ник, аватар слота, рамка и титул под ником", () => {
    render(<PlayerBadge nickname="Аня" appearance={full} />);
    const badge = screen.getByTestId("player-badge");
    expect(within(badge).getByText("Аня")).toBeInTheDocument();
    expect(badge.querySelector('img[src="/cosmetics/fox.webp"]')).not.toBeNull();
    expect(badge.querySelector('img[src="/cosmetics/gold.webp"]')).not.toBeNull();
    expect(within(badge).getByTestId("player-title")).toHaveTextContent("Чемпион");
  });

  it("своя аватарка важнее предмета слота avatar", () => {
    render(
      <PlayerBadge
        nickname="Аня"
        appearance={{ ...full, custom_avatar_url: "/ugc/avatars/a.webp" }}
      />,
    );
    const badge = screen.getByTestId("player-badge");
    expect(badge.querySelector('img[src="/ugc/avatars/a.webp"]')).not.toBeNull();
    expect(badge.querySelector('img[src="/cosmetics/fox.webp"]')).toBeNull();
  });

  it.each([null, undefined, { slots: [] }])("без оформления (%j) — только ник и заглушка", (a) => {
    render(<PlayerBadge nickname="Боря" appearance={a as AppearanceLike | null | undefined} />);
    const badge = screen.getByTestId("player-badge");
    expect(within(badge).getByText("Боря")).toBeInTheDocument();
    expect(badge.querySelector("img")).toBeNull();
    expect(within(badge).queryByTestId("player-title")).toBeNull();
  });

  it("титул и ник выводятся текстом (разметка не исполняется)", () => {
    render(
      <PlayerBadge
        nickname={'<img src=x onerror="alert(1)">'}
        appearance={{ slots: [{ slot: "title", item: item("title", "t", "<b>x</b>") }] }}
      />,
    );
    const badge = screen.getByTestId("player-badge");
    expect(badge.querySelector("b")).toBeNull();
    expect(badge.querySelector("img[onerror]")).toBeNull();
  });
});

describe("ProfileBackdrop", () => {
  it("рисует фон профиля из слота background", () => {
    const { container } = render(
      <ProfileBackdrop appearance={full}>
        <p>карточка</p>
      </ProfileBackdrop>,
    );
    expect(container.querySelector('img[src="/cosmetics/stars.webp"]')).not.toBeNull();
    expect(screen.getByText("карточка")).toBeInTheDocument();
  });

  it("без фона — только содержимое", () => {
    const { container } = render(
      <ProfileBackdrop appearance={null}>
        <p>карточка</p>
      </ProfileBackdrop>,
    );
    expect(container.querySelector("img")).toBeNull();
    expect(screen.getByText("карточка")).toBeInTheDocument();
  });
});
