import { describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import { setupMockBackend, signInAsGuest, world } from "@/test/msw";
import { renderWithProviders } from "@/test/render";
import type { RoundOut } from "@/shared/api/types";
import { RevealDialog } from "./RevealDialog";

setupMockBackend();

const artwork = {
  url: "https://m/cover.jpg",
  expires_at: "2099-01-01T00:00:00Z",
  mime: "image/jpeg",
};

/** Завершённый раунд режима из мок-мира; `extra` дополняет ревил (например, `artwork`). */
function finished(modeKey: string, extra: Record<string, unknown> = {}) {
  const s = world.startSession(signInAsGuest(), world.mode(modeKey)!, null);
  world.giveUp(s, s.rounds[0]!);
  const round = world.snapshot(s).rounds[0]!;
  const withExtra = { ...round, reveal: { ...round.reveal!, ...extra } } as RoundOut;
  return { s, round: withExtra, domain: world.mode(modeKey)!.domain };
}

function show({ s, round, domain }: ReturnType<typeof finished>) {
  renderWithProviders(
    <RevealDialog
      sessionId={s.id}
      round={round}
      presentation="score"
      stats={null}
      last={false}
      onNext={vi.fn()}
      domain={domain}
    />,
  );
}

describe("ответ раунда: кадр финальной подсказки и обложка (ADR 0026)", () => {
  it("ревил blackout — кадр; постер его не подменяет", async () => {
    show(finished("film.random.blackout", { artwork }));
    expect(await screen.findByTestId("reveal-image")).toBeInTheDocument();
    expect(screen.queryByRole("img", { name: /Постер/ })).not.toBeInTheDocument();
  });

  it("кадровый финал без artwork — кадр, как раньше", async () => {
    show(finished("film.random.blackout", { artwork: null }));
    expect(await screen.findByTestId("reveal-image")).toBeInTheDocument();
  });

  it("аудио-финал (музыка) и artwork — обложка вместо кадра, кадра нет", () => {
    show(finished("music.random.classic", { artwork }));
    expect(screen.getByRole("img", { name: /Обложка/ })).toHaveAttribute("src", artwork.url);
    expect(screen.queryByTestId("reveal-image")).not.toBeInTheDocument();
  });

  it("аудио-финал без artwork — заглушка", () => {
    show(finished("music.random.classic", { artwork: null }));
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
    expect(screen.queryByTestId("reveal-image")).not.toBeInTheDocument();
  });
});
