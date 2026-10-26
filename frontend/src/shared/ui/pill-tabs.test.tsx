import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { PillTabs } from "./pill-tabs";

const TABS = [
  { value: "a", label: "Первая" },
  { value: "b", label: "Вторая" },
  { value: "c", label: "Третья" },
] as const;

describe("PillTabs", () => {
  it("выбранная вкладка отмечена и единственная в порядке табуляции", () => {
    render(<PillTabs tabs={TABS} value="b" onChange={() => {}} aria-label="Группы" />);
    expect(screen.getByRole("tab", { name: "Вторая" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tab", { name: "Вторая" })).toHaveAttribute("tabindex", "0");
    expect(screen.getByRole("tab", { name: "Первая" })).toHaveAttribute("tabindex", "-1");
  });

  it("клик и стрелки переключают, с последней вкладки стрелка вправо идёт на первую", async () => {
    const onChange = vi.fn();
    render(<PillTabs tabs={TABS} value="c" onChange={onChange} aria-label="Группы" />);
    await userEvent.click(screen.getByRole("tab", { name: "Первая" }));
    expect(onChange).toHaveBeenLastCalledWith("a");
    screen.getByRole("tab", { name: "Третья" }).focus();
    await userEvent.keyboard("{ArrowRight}");
    expect(onChange).toHaveBeenLastCalledWith("a");
  });
});
