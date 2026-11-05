import { describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "@/test/render";
import { JoinByCode } from "./JoinByCode";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

describe("JoinByCode", () => {
  it("пример кода — явный placeholder («Например, …»), поле пустое", () => {
    renderWithProviders(<JoinByCode />);
    const input = screen.getByTestId("room-code-input");
    expect(input).toHaveValue("");
    expect(input).toHaveAttribute("placeholder", "Например, K7MQ2XWP");
  });
});
