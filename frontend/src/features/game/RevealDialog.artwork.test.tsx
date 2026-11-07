import { describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import { setupMockBackend } from "@/test/msw";
import { renderWithProviders } from "@/test/render";
import type { RoundOut } from "@/shared/api/types";
import { RevealDialog } from "./RevealDialog";

setupMockBackend();

const artwork = {
  url: "https://m/cover.jpg",
  expires_at: "2099-01-01T00:00:00Z",
  mime: "image/jpeg",
};

function round(reveal: Record<string, unknown>): RoundOut {
  return {
    index: 0,
    level: 1,
    levels_count: 5,
    guesses: [{ kind: "guess", display: "x", verdict: "exact", level: 1 }],
    points: 100,
    outcome: "exact",
    reveal: {
      media_item_id: "m1",
      title: "Nevermind",
      year: 1991,
      persons: [{ name: "Nirvana", role: "artist" }],
      final_clue: { kind: "audio", url: "https://m/a.mp3", expires_at: "2099-01-01T00:00:00Z" },
      ...reveal,
    },
  } as unknown as RoundOut;
}

const show = (r: RoundOut, domain: "music" | "film" = "music") =>
  renderWithProviders(
    <RevealDialog
      sessionId="s1"
      round={r}
      presentation="score"
      stats={null}
      last={false}
      onNext={vi.fn()}
      domain={domain}
    />,
  );

describe("ответ раунда: обложка и постер", () => {
  it("artwork есть — показывает обложку", () => {
    show(round({ artwork }));
    expect(screen.getByRole("img", { name: "Обложка «Nevermind»" })).toHaveAttribute(
      "src",
      artwork.url,
    );
  });

  it("кино: постер", () => {
    show(round({ artwork, title: "Сталкер" }), "film");
    expect(screen.getByRole("img", { name: "Постер «Сталкер»" })).toBeInTheDocument();
  });

  it("artwork = null — заглушка без картинки", () => {
    show(round({ artwork: null }));
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
    expect(screen.getByTestId("cover")).toBeInTheDocument();
  });

  it("artwork не пришёл вовсе (старый сервер) — тоже заглушка", () => {
    show(round({}));
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
    expect(screen.getByTestId("reveal-title")).toHaveTextContent("Nevermind");
  });

  it("музыка: Play и перемотки нет, обложка, название, исполнитель, сохранение и «Продолжить» на месте", async () => {
    show(round({ artwork }));
    expect(screen.queryByTestId("clue-audio")).not.toBeInTheDocument();
    expect(screen.queryByTestId("reveal-audio")).not.toBeInTheDocument();
    expect(screen.queryByRole("slider")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Слушать|Пауза|Воспроизвести/ })).toBeNull();
    expect(screen.getByRole("img", { name: "Обложка «Nevermind»" })).toBeInTheDocument();
    expect(screen.getByTestId("reveal-title")).toHaveTextContent("Nevermind");
    expect(screen.getByText("Nirvana · 1991")).toBeInTheDocument();
    expect(await screen.findByText("В плейлист")).toBeInTheDocument();
    expect(screen.getByTestId("reveal-next")).toHaveTextContent("Продолжить");
  });

  it("аудио-ассет ответа не запрашивается", async () => {
    const fetched = vi.spyOn(globalThis, "fetch");
    show(round({ artwork }));
    await new Promise((r) => setTimeout(r, 50));
    expect(fetched.mock.calls.filter(([u]) => String(u).includes("a.mp3"))).toEqual([]);
    fetched.mockRestore();
  });
});
