import { describe, expect, it, vi } from "vitest";
import type { ComponentProps } from "react";
import { fireEvent, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "@/test/render";
import { SeekBar, seekTarget } from "./seek-bar";

const RECT = { left: 100, width: 300 };

describe("seekTarget: позиция по полосе → секунда", () => {
  it("полоса 30 с, открыто 30 с: середина — 15 с", () => {
    expect(seekTarget(250, RECT, 30, 30)).toBe(15);
  });

  it("клик за границей открытой части прижимается к ней", () => {
    expect(seekTarget(380, RECT, 30, 4)).toBe(4);
    expect(seekTarget(110, RECT, 30, 4)).toBe(1);
  });

  it("за краями полосы — 0 и граница; нулевая ширина не ломает", () => {
    expect(seekTarget(0, RECT, 30, 30)).toBe(0);
    expect(seekTarget(9999, RECT, 30, 30)).toBe(30);
    expect(seekTarget(200, { left: 0, width: 0 }, 30, 30)).toBe(0);
  });
});

function bar(props: Partial<ComponentProps<typeof SeekBar>> = {}) {
  const onSeek = vi.fn();
  renderWithProviders(<SeekBar value={0} max={30} open={30} onSeek={onSeek} {...props} />);
  const el = screen.getByRole("slider");
  vi.spyOn(el, "getBoundingClientRect").mockReturnValue({ ...RECT, right: 400 } as DOMRect);
  return { el, onSeek };
}

describe("SeekBar", () => {
  it("клик по полосе перематывает в эту секунду", () => {
    const { el, onSeek } = bar();
    fireEvent.pointerDown(el, { clientX: 250 });
    expect(onSeek).toHaveBeenLastCalledWith(15);
  });

  it("клик за границей уровня прижимается к границе", () => {
    const { el, onSeek } = bar({ open: 4 });
    fireEvent.pointerDown(el, { clientX: 390 });
    expect(onSeek).toHaveBeenLastCalledWith(4);
  });

  it("перетаскивание: движение с нажатой кнопкой перематывает, после отпускания — нет", () => {
    const { el, onSeek } = bar();
    fireEvent.pointerDown(el, { clientX: 130 });
    fireEvent.pointerMove(el, { clientX: 190 });
    expect(onSeek).toHaveBeenLastCalledWith(9);
    fireEvent.pointerUp(el);
    onSeek.mockClear();
    fireEvent.pointerMove(el, { clientX: 300 });
    expect(onSeek).not.toHaveBeenCalled();
  });

  it("стрелки, PageUp/PageDown, Home и End; не дальше открытой части и не меньше нуля", async () => {
    const user = userEvent.setup();
    const { el, onSeek } = bar({ value: 3, open: 5 });
    el.focus();
    await user.keyboard("{ArrowRight}");
    expect(onSeek).toHaveBeenLastCalledWith(4);
    await user.keyboard("{ArrowLeft}");
    expect(onSeek).toHaveBeenLastCalledWith(2);
    await user.keyboard("{PageUp}");
    expect(onSeek).toHaveBeenLastCalledWith(5); // 8 → граница
    await user.keyboard("{PageDown}");
    expect(onSeek).toHaveBeenLastCalledWith(0); // -2 → 0
    await user.keyboard("{End}");
    expect(onSeek).toHaveBeenLastCalledWith(5);
    await user.keyboard("{Home}");
    expect(onSeek).toHaveBeenLastCalledWith(0);
  });

  it("доступность: slider с границами и текстом позиции", () => {
    bar({ value: 7.4, open: 12 });
    const el = screen.getByRole("slider", { name: "Перемотка" });
    expect(el).toHaveAttribute("aria-valuemax", "12");
    expect(el).toHaveAttribute("aria-valuenow", "7");
    expect(el).toHaveAttribute("aria-valuetext", "7 с из 12 с");
  });
});
