import { describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { setupMockBackend, signInAsGuest, world } from "@/test/msw";
import { renderWithProviders } from "@/test/render";
import { RevealDialog } from "./RevealDialog";

setupMockBackend();

function open(onNext = vi.fn()) {
  const s = world.startSession(signInAsGuest(), world.mode("music.random.classic")!, null);
  world.giveUp(s, s.rounds[0]!);
  const round = world.snapshot(s).rounds[0]!;
  renderWithProviders(
    <RevealDialog
      sessionId={s.id}
      round={round}
      presentation="score"
      stats={null}
      last={false}
      onNext={onNext}
      domain="music"
    />,
  );
  return onNext;
}

describe("ответ раунда: клавиши и полоса перемотки", () => {
  it("пробел на сфокусированной полосе не закрывает ответ", async () => {
    const user = userEvent.setup();
    const onNext = open();
    const bar = await screen.findByRole("slider");
    bar.focus();
    await user.keyboard(" ");
    expect(onNext).not.toHaveBeenCalled();
  });

  it("пробел вне полосы продолжает, как и раньше", async () => {
    const user = userEvent.setup();
    const onNext = open();
    await screen.findByRole("slider");
    (document.activeElement as HTMLElement | null)?.blur();
    await user.keyboard(" ");
    expect(onNext).toHaveBeenCalledTimes(1);
  });
});
