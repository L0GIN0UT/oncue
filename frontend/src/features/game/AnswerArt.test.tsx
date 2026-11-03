import { describe, expect, it } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "@/test/render";
import { AnswerArt } from "./AnswerArt";

const artwork = {
  url: "https://m/cover.jpg",
  expires_at: "2099-01-01T00:00:00Z",
  mime: "image/jpeg",
};

describe("AnswerArt", () => {
  it("музыка: обложка с подписью", () => {
    renderWithProviders(<AnswerArt domain="music" artwork={artwork} title="Nevermind" />);
    expect(screen.getByRole("img", { name: "Обложка «Nevermind»" })).toHaveAttribute(
      "src",
      artwork.url,
    );
  });

  it("кино: постер с подписью и формой 2:3", () => {
    renderWithProviders(<AnswerArt domain="film" artwork={artwork} title="Сталкер" />);
    expect(screen.getByRole("img", { name: "Постер «Сталкер»" })).toBeInTheDocument();
    expect(screen.getByTestId("cover").className).toContain("aspect-[2/3]");
  });

  it("artwork = null — заглушка домена, картинки нет", () => {
    renderWithProviders(<AnswerArt domain="film" artwork={null} title="Сталкер" />);
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
    expect(screen.getByTestId("cover")).toBeInTheDocument();
  });
});
