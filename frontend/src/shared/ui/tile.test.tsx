import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Tile } from "./tile";

describe("Tile", () => {
  it("рисует капитель, дополнение, заголовок, подпись и содержимое", () => {
    render(
      <Tile cap="Соло" aside={<i>30 режимов</i>} title="Чем ещё сыграть" subtitle="Архив: 29 дней">
        <p>слот</p>
      </Tile>,
    );
    expect(screen.getByText("Соло")).toBeInTheDocument();
    expect(screen.getByText("30 режимов")).toBeInTheDocument();
    expect(screen.getByText("Чем ещё сыграть")).toBeInTheDocument();
    expect(screen.getByText("Архив: 29 дней")).toBeInTheDocument();
    expect(screen.getByText("слот")).toBeInTheDocument();
  });

  it("с href — ссылка, без — блок", () => {
    render(
      <>
        <Tile href="/solo" title="Ссылка" />
        <Tile title="Блок" data-testid="plain" />
      </>,
    );
    expect(screen.getByRole("link", { name: "Ссылка" })).toHaveAttribute("href", "/solo");
    expect(screen.getByTestId("plain").tagName).toBe("DIV");
  });

  it("акцентная плитка получает янтарную обводку и янтарную капитель", () => {
    render(<Tile accent cap="Событие" data-testid="t" />);
    expect(screen.getByTestId("t").className).toContain("border-music");
    expect(screen.getByText("Событие").className).toContain("text-music");
  });

  it("обычная плитка — серая обводка", () => {
    render(<Tile data-testid="t" />);
    expect(screen.getByTestId("t").className).toContain("border-rule");
    expect(screen.getByTestId("t").className).not.toContain("border-music");
  });
});
