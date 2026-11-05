/**
 * Смена уровня («Пропустить») не должна размонтировать загадку: тот же DOM-узел плеера или кадра
 * живёт всё время, а вместо него не появляется заглушка «Загрузка…» (она на ~80 мс схлопывала
 * высоту страницы и сбрасывала прокрутку). Проверяется итог, а не тайминг: наблюдатель за DOM
 * ловит любое удаление узла, поэтому тест не зависит от скорости загрузки следующего уровня.
 */
import { describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { signInAsGuest, setupMockBackend, world } from "@/test/msw";
import { renderWithProviders } from "@/test/render";
import { GameScreen } from "./GameScreen";

vi.mock("next/navigation", () => ({ useRouter: () => ({ replace: vi.fn(), push: vi.fn() }) }));

setupMockBackend();

/** Следит за удалением узлов и появлением заглушки загрузки загадки. */
function watchDom(tracked: Element[]) {
  const removed: Element[] = [];
  let placeholder = 0;
  const observer = new MutationObserver((records) => {
    for (const r of records) {
      r.removedNodes.forEach((n) => {
        if (n instanceof Element && tracked.some((t) => n === t || n.contains(t))) removed.push(n);
      });
      r.addedNodes.forEach((n) => {
        if (n instanceof Element && n.textContent?.includes("Загружаем")) placeholder += 1;
      });
    }
  });
  observer.observe(document.body, { childList: true, subtree: true });
  return {
    removed,
    get placeholder() {
      return placeholder;
    },
    stop: () => observer.disconnect(),
  };
}

const start = (key: string) => world.startSession(signInAsGuest(), world.mode(key)!, null);

describe("GameScreen — загадка не пропадает при смене уровня", () => {
  it.each([
    ["music.random.classic", "clue-audio"],
    ["film.random.frame", "clue-image"],
  ])("%s: узел %s переживает «Пропустить»", async (key, testId) => {
    const user = userEvent.setup();
    const s = start(key);
    renderWithProviders(<GameScreen sessionId={s.id} />);
    const node = await screen.findByTestId(testId);
    const srcBefore = node.getAttribute("src");
    const watcher = watchDom([node]);

    await user.click(screen.getByTestId("skip"));
    // Итог: открыт следующий уровень (в историю записан пропуск) и загадка сменилась.
    await waitFor(() =>
      expect(screen.getByTestId("attempts").querySelector('[data-result="skip"]')).not.toBeNull(),
    );
    await waitFor(() => expect(screen.getByTestId(testId).getAttribute("src")).not.toBe(srcBefore));
    watcher.stop();

    expect(watcher.removed).toHaveLength(0);
    expect(watcher.placeholder).toBe(0);
    expect(screen.getByTestId(testId)).toBe(node);
    expect(node.isConnected).toBe(true);
  });
});
