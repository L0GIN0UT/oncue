/** Игровой экран на режимах этапа 3a (мок-бэкенд по контрактам, MSW). */
import { describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http } from "msw";
import { server, signInAsGuest, setupMockBackend, world } from "@/test/msw";
import { renderWithGameShell, renderWithProviders } from "@/test/render";
import { itemById, personId } from "../../../e2e/mocks/world";
import { GameScreen } from "./GameScreen";

const replace = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace, push: vi.fn() }) }));

setupMockBackend();

const start = (key: string, subjectIds: string[] = []) =>
  world.startSession(signInAsGuest(), world.mode(key)!, null, subjectIds);

describe("GameScreen — режимы этапа 3a", () => {
  it("описание: уровень — текстовый JSON-ассет, текст на экране", async () => {
    const s = start("film.random.description");
    renderWithProviders(<GameScreen sessionId={s.id} />);
    expect(await screen.findByTestId("text-clue")).toHaveTextContent(/Синтетическая история №\d+/);
    expect(screen.getByTestId("give-up")).toBeEnabled();
  });

  it("эмодзи: крупные эмодзи с подписью", async () => {
    const s = start("film.random.emoji");
    renderWithProviders(<GameScreen sessionId={s.id} />);
    expect(await screen.findByRole("img", { name: /^Эмодзи \(1\)/ })).toBeInTheDocument();
  });

  it("звук в темноте: плеер на чёрной сцене, ревил — кадр из Blob", async () => {
    const user = userEvent.setup();
    const s = start("film.random.blackout");
    renderWithProviders(<GameScreen sessionId={s.id} />);
    expect(await screen.findByTestId("clue-scene")).toHaveAttribute("data-scene", "blackout");
    expect(screen.getByTestId("clue-audio")).toHaveAttribute(
      "src",
      expect.stringMatching(/^blob:/),
    );
    await user.click(screen.getByTestId("give-up"));
    expect(await screen.findByTestId("reveal-image")).toHaveAttribute(
      "src",
      expect.stringMatching(/^blob:/),
    );
  });

  it("регресс хвоста D13: старая ссылка ревила 403 → кадр со свежей (один final-clue)", async () => {
    const user = userEvent.setup();
    world.scenario.staleRevealLinks = true;
    const s = start("film.random.frame");
    renderWithProviders(<GameScreen sessionId={s.id} />);
    await user.click(await screen.findByTestId("give-up"));
    expect(await screen.findByTestId("reveal-image")).toHaveAttribute(
      "src",
      expect.stringMatching(/^blob:/),
    );
    expect(world.finalClueCalls).toBe(1);
  });

  it("марафон: жизни из progress, сдача отнимает жизнь", async () => {
    const user = userEvent.setup();
    const s = start("film.marathon.director", [personId("Андрей Тарковский")]);
    renderWithGameShell(<GameScreen sessionId={s.id} />);
    expect(await screen.findByTestId("lives")).toHaveTextContent("3");
    // 5 фильмов марафона — индикатор точками; счёт идёт очками.
    expect(await screen.findByTestId("score")).toBeInTheDocument();
    await user.click(screen.getByTestId("give-up"));
    await user.click(await screen.findByTestId("reveal-next"));
    expect(await screen.findByTestId("lives")).toHaveTextContent("2");
  });

  it("больше/меньше: метрика опорной, клавиша 1, варианты недоступны во время хода, метрика после", async () => {
    const user = userEvent.setup();
    let release!: () => void;
    const gate = new Promise<void>((resolve) => (release = resolve));
    // Ход «висит», пока тест не отпустит его; затем — обычный обработчик мока.
    server.use(http.post("*/api/game/v1/sessions/:id/guesses", () => gate.then(() => undefined)));
    const s = start("film.pair.rating");
    renderWithProviders(<GameScreen sessionId={s.id} />);
    expect(await screen.findByTestId("shown-metric")).toHaveTextContent(/\d/);
    expect(screen.getByTestId("streak")).toHaveTextContent("0");
    expect(screen.getByTestId("lives")).toHaveTextContent("1");
    expect(screen.queryByTestId("give-up")).not.toBeInTheDocument();
    // Загадка уровня — кадр загаданной (варианты — подписи); очков у серии нет.
    expect(await screen.findByTestId("clue-image")).toBeInTheDocument();
    expect(screen.queryByTestId("score")).not.toBeInTheDocument();

    await user.keyboard("1");
    await waitFor(() => expect(screen.getByTestId("pick-option-higher")).toBeDisabled());
    expect(screen.getByTestId("pick-option-lower")).toBeDisabled();
    release();
    expect(await screen.findByTestId("reveal-metric")).toHaveTextContent(/\d/);
    expect(s.rounds[0]!.guesses).toHaveLength(1);
  });

  it("сетка: тур, два кадра-варианта без загадки уровня, «согласны N %» после выбора", async () => {
    const user = userEvent.setup();
    const s = start("film.bracket.weekly");
    renderWithProviders(<GameScreen sessionId={s.id} />);
    const stage = await screen.findByTestId("bracket-stage");
    expect(stage).toHaveTextContent("1/4 финала");
    expect(stage).toHaveTextContent("пара 1 из 4");
    const options = screen.getAllByTestId(/^pick-option-/);
    expect(options).toHaveLength(2);
    expect(within(options[0]!).getByTestId("shown-image")).toBeInTheDocument();
    expect(screen.queryByTestId("clue-image")).not.toBeInTheDocument();

    await user.click(screen.getByTestId("pick-option-b"));
    expect(await screen.findByTestId("agree-pct")).toHaveTextContent(/Согласны \d+ % игроков/);
    const picked = itemById(s.rounds[0]!.options[1]!.itemId!)!;
    expect(screen.getByTestId("reveal-choice")).toHaveTextContent(picked.title);
    await user.click(screen.getByTestId("reveal-next"));
    expect(await screen.findByTestId("bracket-stage")).toHaveTextContent("пара 2 из 4");
    expect(screen.queryByTestId("agree-pct")).not.toBeInTheDocument();
  });
});
