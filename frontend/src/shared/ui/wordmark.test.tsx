import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { Wordmark } from "./wordmark";

describe("Wordmark", () => {
  it("«Guess» и «verse» — отдельные элементы с цветом текста и акцента", () => {
    render(<Wordmark />);
    expect(screen.getByTestId("wordmark-guess")).toHaveTextContent(/^Guess$/);
    expect(screen.getByTestId("wordmark-guess").className).toContain("text-ink");
    expect(screen.getByTestId("wordmark-verse")).toHaveTextContent(/^verse$/);
    expect(screen.getByTestId("wordmark-verse").className).toContain("text-music");
  });

  it("логотип рендерится декоративно, слово читается целиком", () => {
    const { container } = render(<Wordmark />);
    const logo = screen.getByTestId("wordmark-logo");
    expect(logo).toHaveAttribute("src", "/logo.svg");
    expect(logo).toHaveAttribute("aria-hidden", "true");
    expect(container).toHaveTextContent("Guessverse");
  });
});
