import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { Avatar, initialsOf } from "./avatar";
import { Row } from "./row";
import { StatTile } from "./stat-tile";

describe("Row", () => {
  it("со ссылкой — ссылка, с обработчиком — кнопка, иначе — блок", async () => {
    const onClick = vi.fn();
    render(
      <>
        <Row href="/solo" trailing="12">
          Соло
        </Row>
        <Row onClick={onClick}>Кнопка</Row>
        <Row>Просто строка</Row>
      </>,
    );
    expect(screen.getByRole("link", { name: /Соло/ })).toHaveAttribute("href", "/solo");
    await userEvent.click(screen.getByRole("button", { name: "Кнопка" }));
    expect(onClick).toHaveBeenCalledOnce();
    expect(screen.getByText("Просто строка").closest("a,button")).toBeNull();
  });

  it("выбранная строка получает янтарную рамку, высота lg больше md", () => {
    render(
      <>
        <Row selected>Выбрана</Row>
        <Row size="lg">Высокая</Row>
      </>,
    );
    expect(screen.getByText("Выбрана").parentElement!.className).toContain("border-music");
    expect(screen.getByText("Высокая").parentElement!.className).toContain("min-h-[68px]");
  });
});

describe("StatTile", () => {
  it("число и подпись; акцент только по флагу", () => {
    render(
      <>
        <StatTile value="13" label="дней серия" accent />
        <StatTile value="1 480" label="рейтинг" />
      </>,
    );
    expect(screen.getByText("13").className).toContain("text-music");
    expect(screen.getByText("1 480").className).not.toContain("text-music");
  });
});

describe("Avatar", () => {
  it.each([
    ["Иван Логин", "ИЛ"],
    ["newbie", "NE"],
    ["  ", "?"],
    [null, "?"],
  ])("инициалы для %j — %s", (name, expected) => {
    expect(initialsOf(name)).toBe(expected);
  });

  it("с картинкой показывает её", () => {
    const { container } = render(<Avatar name="Иван" src="/media/a.png" />);
    expect(container.querySelector("img")).toHaveAttribute("src", "/media/a.png");
  });
});
