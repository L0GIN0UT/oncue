/** Итоги: обложки и постеры строк (artwork из bff) и перевыдача ссылки при истечении. */
import { describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { signInAsGuest, setupMockBackend, world } from "@/test/msw";
import { renderWithProviders } from "@/test/render";
import type { MockSession } from "../../../e2e/mocks/world";
import { ResultsView } from "./ResultsView";

vi.mock("next/navigation", () => ({ useRouter: () => ({ replace: vi.fn(), push: vi.fn() }) }));

setupMockBackend();

function finished(): MockSession {
  const s = world.startSession(signInAsGuest(), world.mode("music.daily.classic")!, null);
  for (let guard = 0; s.status === "active" && guard < 50; guard++)
    world.giveUp(
      s,
      s.rounds.find((x) => x.index === s.current)!,
    );
  return s;
}

describe("ResultsView: обложки строк", () => {
  it("у каждой строки обложка из artwork; без artwork — заглушка, без картинки", async () => {
    world.scenario.revealArtwork = true;
    renderWithProviders(<ResultsView sessionId={finished().id} />);
    const rows = await screen.findAllByTestId("result-row");
    expect(rows).toHaveLength(5);
    for (const row of rows) {
      expect(within(row).getByRole("presentation", { hidden: true })).toHaveAttribute(
        "src",
        expect.stringMatching(/^\/media\/a\//),
      );
    }
  });

  it("artwork = null: заглушка домена, картинки нет", async () => {
    world.scenario.revealArtwork = false;
    renderWithProviders(<ResultsView sessionId={finished().id} />);
    const rows = await screen.findAllByTestId("result-row");
    for (const row of rows) expect(row.querySelector("img")).toBeNull();
  });

  it("ссылка не открылась: одна перевыдача, новая ссылка на экране, без цикла", async () => {
    world.scenario.revealArtwork = true;
    world.scenario.staleArtworkLinks = true;
    renderWithProviders(<ResultsView sessionId={finished().id} />);
    const first = (await screen.findAllByTestId("result-row"))[0]!;
    const img = within(first).getByRole("presentation", { hidden: true });
    const stale = img.getAttribute("src");
    fireEvent.error(img);
    await waitFor(() => expect(world.artworkCalls).toBe(1));
    await waitFor(() =>
      expect(
        within(first).getByRole("presentation", { hidden: true }).getAttribute("src"),
      ).not.toBe(stale),
    );
    // Повторная ошибка уже свежей ссылки новую выдачу не запрашивает (иначе цикл).
    fireEvent.error(within(first).getByRole("presentation", { hidden: true }));
    expect(world.artworkCalls).toBe(1);
    expect(first.querySelector("img")).toBeNull();
  });
});
