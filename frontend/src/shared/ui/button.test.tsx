import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Button } from "./button";

describe("Button", () => {
  it.each(["primary", "secondary", "ghost", "quiet", "icon"] as const)(
    "вариант %s рисуется",
    (variant) => {
      render(<Button variant={variant}>Нажать</Button>);
      expect(screen.getByRole("button", { name: "Нажать" })).toBeEnabled();
    },
  );

  it("primary — единственный с заливкой акцента", () => {
    render(
      <>
        <Button>Главная</Button>
        <Button variant="secondary">Вторая</Button>
      </>,
    );
    expect(screen.getByRole("button", { name: "Главная" }).className).toContain("bg-music");
    expect(screen.getByRole("button", { name: "Вторая" }).className).not.toContain("bg-music");
  });

  it("icon — тап-цель 44 px и подпись через aria-label", () => {
    render(<Button variant="icon" aria-label="Меню" />);
    expect(screen.getByRole("button", { name: "Меню" }).className).toContain("size-11");
  });

  it("disabled не кликается", () => {
    render(<Button disabled>Нельзя</Button>);
    expect(screen.getByRole("button", { name: "Нельзя" })).toBeDisabled();
  });

  it("asChild отдаёт стили ссылке", () => {
    render(
      <Button asChild>
        <a href="/x">Ссылка</a>
      </Button>,
    );
    expect(screen.getByRole("link", { name: "Ссылка" }).className).toContain("border-[1.5px]");
  });
});
