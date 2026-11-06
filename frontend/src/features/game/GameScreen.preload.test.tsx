/**
 * Предзагрузка уровня 1 следующего раунда (`next_round_first_clue`, #114): файл качается, пока
 * открыт ответ раунда, а следующий раунд стартует обычным запросом `POST .../clue` — он и запускает
 * таймер на сервере; ссылка раунд не открывает.
 */
import { describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { server, signInAsGuest, setupMockBackend, world } from "@/test/msw";
import { renderWithProviders } from "@/test/render";
import { GameScreen } from "./GameScreen";

vi.mock("next/navigation", () => ({ useRouter: () => ({ replace: vi.fn(), push: vi.fn() }) }));

setupMockBackend();

function track() {
  const media: string[] = [];
  const clues: string[] = [];
  server.events.on("request:start", ({ request }) => {
    const url = new URL(request.url);
    if (request.method === "GET" && url.pathname.startsWith("/media/a/")) media.push(url.pathname);
    if (request.method === "POST" && /\/rounds\/\d+\/clue$/.test(url.pathname))
      clues.push(url.pathname);
  });
  return { media, clues };
}

/** Закрыть раунд: пропуски до последнего уровня, там «Сдаться». */
async function giveUpRound() {
  for (let guard = 0; guard < 10; guard++) {
    const giveUp = screen.queryByTestId("give-up");
    if (giveUp && !(giveUp as HTMLButtonElement).disabled) {
      await userEvent.click(giveUp);
      return;
    }
    const skip = await screen.findByTestId("skip");
    await waitFor(() => expect(skip).toBeEnabled());
    await userEvent.click(skip);
  }
  throw new Error("«Сдаться» не появилась");
}

vi.setConfig({ testTimeout: 20_000 });

describe("GameScreen: предзагрузка следующего раунда", () => {
  it("файл уровня 1 качается на ответе раунда, а раунд стартует обычным POST /clue", async () => {
    world.scenario.nextRoundPreload = true;
    const s = world.startSession(signInAsGuest(), world.mode("music.random.classic")!, null);
    const seen = track();
    renderWithProviders(<GameScreen sessionId={s.id} />);
    await screen.findByTestId("clue-audio");
    await giveUpRound();
    await screen.findByTestId("reveal");

    // Подпись в ссылке мока каждый раз новая, файл — тот же путь.
    const preloaded = new URL(world.snapshot(s).next_round_first_clue!.url, "http://x").pathname;
    // Пока открыт ответ: файл следующего раунда уже скачан, а раунд ещё не открыт запросом.
    await waitFor(() => expect(seen.media).toContain(preloaded));
    expect(seen.clues.filter((p) => p.includes("/rounds/1/"))).toHaveLength(0);

    await userEvent.click(screen.getByTestId("reveal-next"));
    const audio = await screen.findByTestId("clue-audio");
    expect(audio).toHaveAttribute("src", expect.stringMatching(/^blob:/));
    // Таймер: уровень открыт обычным запросом, файл повторно не качался.
    expect(seen.clues.filter((p) => p.includes("/rounds/1/"))).toHaveLength(1);
    expect(seen.media.filter((u) => u === preloaded)).toHaveLength(1);
  });

  it("без флага (поля нет): раунд стартует как раньше, одним POST /clue", async () => {
    world.scenario.nextRoundPreload = false;
    const s = world.startSession(signInAsGuest(), world.mode("music.random.classic")!, null);
    const seen = track();
    renderWithProviders(<GameScreen sessionId={s.id} />);
    await screen.findByTestId("clue-audio");
    await giveUpRound();
    await screen.findByTestId("reveal");
    expect(world.snapshot(s).next_round_first_clue).toBeNull();
    await userEvent.click(screen.getByTestId("reveal-next"));
    expect(await screen.findByTestId("clue-audio")).toHaveAttribute(
      "src",
      expect.stringMatching(/^blob:/),
    );
    expect(seen.clues.filter((p) => p.includes("/rounds/1/"))).toHaveLength(1);
  });

  it("предзагрузка не удалась (ссылка не открылась): следующий раунд всё равно стартует", async () => {
    world.scenario.nextRoundPreload = true;
    const s = world.startSession(signInAsGuest(), world.mode("music.random.classic")!, null);
    renderWithProviders(<GameScreen sessionId={s.id} />);
    await screen.findByTestId("clue-audio");
    world.assets.clear();
    await giveUpRound();
    await screen.findByTestId("reveal");
    await userEvent.click(screen.getByTestId("reveal-next"));
    expect(await screen.findByTestId("clue-audio")).toBeInTheDocument();
  });
});
