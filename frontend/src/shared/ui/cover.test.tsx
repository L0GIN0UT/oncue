import { describe, expect, it } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { Cover } from "./cover";

describe("Cover", () => {
  it("с картинкой показывает её, без — заглушку", () => {
    const { rerender } = render(<Cover src="https://m/a.jpg" alt="Обложка" placeholder="♪" />);
    expect(screen.getByRole("img", { name: "Обложка" })).toHaveAttribute("src", "https://m/a.jpg");
    rerender(<Cover src={null} alt="Обложка" placeholder="♪" />);
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
    expect(screen.getByText("♪")).toBeInTheDocument();
  });

  it("ссылка не открылась (истекла) — заглушка; новая ссылка снова пробуется", () => {
    const { rerender } = render(<Cover src="https://m/old.jpg" alt="Обложка" placeholder="♪" />);
    fireEvent.error(screen.getByRole("img"));
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
    expect(screen.getByText("♪")).toBeInTheDocument();
    rerender(<Cover src="https://m/new.jpg" alt="Обложка" placeholder="♪" />);
    expect(screen.getByRole("img")).toHaveAttribute("src", "https://m/new.jpg");
  });
});
