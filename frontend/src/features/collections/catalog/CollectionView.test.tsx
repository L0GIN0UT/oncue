import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { delay, http, HttpResponse } from "msw";
import { server, setupMockBackend, signInAsGuest, signInAsUser, world } from "@/test/msw";
import { renderWithProviders } from "@/test/render";
import { AUTHOR_ID } from "../../../../e2e/mocks/ugc";
import { CollectionView } from "./CollectionView";

const push = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace: vi.fn(), push }) }));

setupMockBackend();

/** UGC-сборник автора `author` из мок-мира (2 лайка, состав из фильмов Тарковского). */
const ugcCollection = () => world.ugc.collections[1]!;

describe("CollectionView", () => {
  beforeEach(() => push.mockClear());

  it("лайк оптимистичен: кнопка и счётчик меняются до ответа сервера", async () => {
    const user = userEvent.setup();
    signInAsUser();
    const c = ugcCollection();
    let release!: () => void;
    const gate = new Promise<void>((r) => (release = r));
    server.use(
      http.put("*/api/collections/v1/collections/:id/like", async () => {
        await gate;
        return HttpResponse.json({ liked: true, likes_count: 3 });
      }),
    );
    renderWithProviders(<CollectionView collectionId={c.id} />);
    const like = await screen.findByTestId("collection-like");
    expect(screen.getByTestId("collection-likes-count")).toHaveTextContent("2");
    await user.click(like);
    expect(like).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByTestId("collection-likes-count")).toHaveTextContent("3");
    release();
    await waitFor(() => expect(like).not.toBeDisabled());
    expect(screen.getByTestId("collection-likes-count")).toHaveTextContent("3");
  });

  it("ошибка лайка откатывает состояние", async () => {
    const user = userEvent.setup();
    signInAsUser();
    const c = ugcCollection();
    world.ugc.scenario.likeFailsOnce = true;
    renderWithProviders(<CollectionView collectionId={c.id} />);
    const like = await screen.findByTestId("collection-like");
    await user.click(like);
    await waitFor(() => expect(like).toHaveAttribute("aria-pressed", "false"));
    expect(screen.getByTestId("collection-likes-count")).toHaveTextContent("2");
    expect(c.likes.size).toBe(2);
  });

  it("избранное: aria-pressed по ответу сервера", async () => {
    const user = userEvent.setup();
    const subject = signInAsUser();
    const c = ugcCollection();
    server.use(
      http.put("*/api/collections/v1/collections/:id/favorite", async () => {
        await delay(20);
        c.favorites.add(subject);
        return HttpResponse.json({ favorite: true });
      }),
    );
    renderWithProviders(<CollectionView collectionId={c.id} />);
    const fav = await screen.findByTestId("collection-favorite");
    await user.click(fav);
    expect(fav).toHaveAttribute("aria-pressed", "true");
    await waitFor(() => expect(fav).not.toBeDisabled());
    expect(c.favorites.has(subject)).toBe(true);
  });

  it("гость: лайк и избранное — ссылки на регистрацию, состава нет", async () => {
    signInAsGuest();
    const c = ugcCollection();
    renderWithProviders(<CollectionView collectionId={c.id} />);
    expect(await screen.findByTestId("collection-like")).toHaveAttribute("href", "/register");
    expect(screen.getByTestId("collection-favorite")).toHaveAttribute("href", "/register");
    expect(screen.queryByTestId("collection-items")).not.toBeInTheDocument();
    expect(screen.getByTestId("collection-author")).toHaveTextContent("author");
  });

  it("автор видит состав и свои эмодзи; лайкнуть свой сборник нельзя", async () => {
    signInAsUser(AUTHOR_ID);
    const c = ugcCollection();
    renderWithProviders(<CollectionView collectionId={c.id} />);
    const items = await screen.findByTestId("collection-items");
    expect(items.querySelectorAll("[data-testid^=collection-entry-]")).toHaveLength(c.items.length);
    expect(items).toHaveTextContent("🌊🚀🧠🌌👁️🕯️");
    expect(screen.getByTestId("collection-like")).toBeDisabled();
  });

  it("прошедший игрок: «пройден» и состав открыт", async () => {
    const subject = signInAsUser();
    const c = ugcCollection();
    c.completed.add(subject);
    renderWithProviders(<CollectionView collectionId={c.id} />);
    expect(await screen.findByTestId("collection-completed")).toBeInTheDocument();
    expect(screen.getByTestId("collection-items")).toBeInTheDocument();
  });

  it("режим «N из M», выбор раундов и старт через /play/{mode}", async () => {
    const user = userEvent.setup();
    signInAsUser();
    const c = world.ugc.collections[0]!;
    renderWithProviders(<CollectionView collectionId={c.id} />);
    const frame = await screen.findByTestId("collection-mode-film.collection.frame");
    expect(frame).toHaveTextContent("8 из 8");
    expect(frame).toHaveAttribute("aria-pressed", "true");
    // В режиме эмодзи у кураторского сборника без своих эмодзи играбельных нет.
    expect(screen.getByTestId("collection-mode-film.collection.emoji")).toBeDisabled();
    expect(screen.getByTestId("collection-rounds-25")).toBeDisabled();
    await user.click(screen.getByTestId("collection-rounds-all"));
    await user.click(screen.getByTestId("collection-play"));
    expect(push).toHaveBeenCalledWith(`/play/film.collection.frame?collection=${c.id}&rounds=all`);
  });

  it("нет сборника — 404 экрана", async () => {
    renderWithProviders(<CollectionView collectionId="00000000-0000-4000-a000-000000009999" />);
    expect(await screen.findByText("Такого сборника нет")).toBeInTheDocument();
  });
});
