import { useState } from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ComboField, type ComboOption } from "./combo-field";

const OPTIONS: ComboOption[] = [
  { id: "1", label: "Creep" },
  { id: "2", label: "Karma Police" },
];

function Demo({ onSelect }: { onSelect: (o: ComboOption) => void }) {
  const [v, setV] = useState("");
  return (
    <ComboField
      label="Ответ"
      clearLabel="Очистить"
      value={v}
      onChange={setV}
      options={v ? OPTIONS : []}
      onSelect={onSelect}
    />
  );
}

describe("ComboField", () => {
  it("подсказки появляются при вводе и раскрываются вверх от поля", async () => {
    render(<Demo onSelect={vi.fn()} />);
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
    await userEvent.type(screen.getByRole("combobox", { name: "Ответ" }), "rad");
    expect(screen.getByRole("listbox").className).toContain("bottom-[calc(100%+4px)]");
    expect(screen.getAllByRole("option")).toHaveLength(2);
  });

  it("Enter выбирает первую подсказку, стрелка вниз — следующую", async () => {
    const onSelect = vi.fn();
    render(<Demo onSelect={onSelect} />);
    const input = screen.getByRole("combobox", { name: "Ответ" });
    await userEvent.type(input, "r{Enter}");
    expect(onSelect).toHaveBeenLastCalledWith(OPTIONS[0]);
    await userEvent.type(input, "a{ArrowDown}{Enter}");
    expect(onSelect).toHaveBeenLastCalledWith(OPTIONS[1]);
  });

  it("клик по подсказке выбирает её и закрывает список", async () => {
    const onSelect = vi.fn();
    render(<Demo onSelect={onSelect} />);
    await userEvent.type(screen.getByRole("combobox"), "k");
    await userEvent.click(screen.getByRole("option", { name: "Karma Police" }));
    expect(onSelect).toHaveBeenCalledWith(OPTIONS[1]);
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
  });

  it("крестик очищает поле", async () => {
    render(<Demo onSelect={vi.fn()} />);
    const input = screen.getByRole("combobox");
    await userEvent.type(input, "abc");
    await userEvent.click(screen.getByRole("button", { name: "Очистить" }));
    expect(input).toHaveValue("");
    expect(screen.queryByRole("button", { name: "Очистить" })).not.toBeInTheDocument();
  });

  it("Escape закрывает список, не очищая поле", async () => {
    render(<Demo onSelect={vi.fn()} />);
    const input = screen.getByRole("combobox");
    await userEvent.type(input, "ab{Escape}");
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
    expect(input).toHaveValue("ab");
  });
});
