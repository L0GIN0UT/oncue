import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, screen } from "@testing-library/react";
import { renderWithProviders } from "@/test/render";
import { NO_SHOW_REDIRECT_MS, NoShowNotice } from "./NoShowNotice";

const replace = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace, push: vi.fn() }),
  usePathname: () => "/room/K7MQ2XWP",
}));

beforeEach(() => {
  vi.useFakeTimers();
  replace.mockReset();
});
afterEach(() => vi.useRealTimers());

describe("пара распалась: соперник не подключился", () => {
  it("говорит причину и возвращает в хаб, где поиск продолжается", () => {
    renderWithProviders(<NoShowNotice />);
    expect(screen.getByTestId("room-no-show")).toHaveTextContent(
      "Соперник не подключился, ищем нового",
    );
    expect(replace).not.toHaveBeenCalled();
    act(() => void vi.advanceTimersByTime(NO_SHOW_REDIRECT_MS));
    expect(replace).toHaveBeenCalledWith("/online");
  });

  it("размонтирование отменяет переход", () => {
    const view = renderWithProviders(<NoShowNotice />);
    view.unmount();
    act(() => void vi.advanceTimersByTime(NO_SHOW_REDIRECT_MS * 2));
    expect(replace).not.toHaveBeenCalled();
  });
});
