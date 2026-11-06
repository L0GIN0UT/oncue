import { describe, expect, it } from "vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import type { CollectionCardOut } from "@/shared/api/types";
import { setupMockBackend, world } from "@/test/msw";
import { renderWithProviders } from "@/test/render";
import { CollectionCard } from "./CollectionCard";

setupMockBackend();

const ID = "00000000-0000-4000-a000-000000000001";

function card(extra: Partial<CollectionCardOut> = {}): CollectionCardOut {
  const col = world.ugc.find(ID)!;
  return { ...world.ugc.card(col, world.users), ...extra } as CollectionCardOut;
}

const art = (url: string) => ({
  url,
  expires_at: new Date(Date.now() + 3_600_000).toISOString(),
  mime: "image/webp",
});

const image = () => screen.queryByTestId("collection-cover-image");

describe("CollectionCard: обложка", () => {
  it("есть cover_artwork: картинка вместо пресета, декоративная (alt пустой)", () => {
    renderWithProviders(
      <ul>
        <CollectionCard
          card={card({ cover_artwork: art("/media/a/cover.webp?md5=x&expires=1") })}
        />
      </ul>,
    );
    expect(image()).toHaveAttribute("src", "/media/a/cover.webp?md5=x&expires=1");
    expect(image()).toHaveAttribute("alt", "");
  });

  it("cover_artwork = null: пресет, картинки нет", () => {
    renderWithProviders(
      <ul>
        <CollectionCard card={card({ cover_artwork: null })} />
      </ul>,
    );
    expect(image()).toBeNull();
    expect(screen.getByTestId(`collection-card-${ID}`)).toBeInTheDocument();
  });

  it("небезопасный адрес не попадает в <img>", () => {
    renderWithProviders(
      <ul>
        <CollectionCard card={card({ cover_artwork: art("https://evil.example/x.png") })} />
      </ul>,
    );
    expect(image()).toBeNull();
  });

  it("проп artwork (пачка covers) подставляется в карточку из collections", () => {
    renderWithProviders(
      <ul>
        <CollectionCard card={card()} artwork={art("/media/a/b.webp")} />
      </ul>,
    );
    expect(image()).toHaveAttribute("src", "/media/a/b.webp");
  });

  it("картинка не открылась: одна перевыдача, вторая ошибка оставляет пресет", async () => {
    renderWithProviders(
      <ul>
        <CollectionCard card={card({ cover_artwork: art("/media/a/stale.webp") })} />
      </ul>,
    );
    fireEvent.error(image()!);
    await waitFor(() => expect(image()?.getAttribute("src")).toMatch(/^\/media\/a\/.+\.svg/));
    expect(world.ugc.coverCalls).toEqual([[ID]]);
    fireEvent.error(image()!);
    await waitFor(() => expect(image()).toBeNull());
    expect(world.ugc.coverCalls).toEqual([[ID]]);
  });
});
