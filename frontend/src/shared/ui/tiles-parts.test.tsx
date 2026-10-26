/** Мелкие примитивы хабов: ListRow, Cover, SectionCap, TierBadge, DailyBadge. */
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Cover } from "./cover";
import { DailyBadge, dailyOutcome } from "./daily-badge";
import { ListRow } from "./list-row";
import { SectionCap } from "./section-cap";
import { TIER_KEYS, TierBadge, tierKeyOf } from "./tier-badge";

describe("ListRow", () => {
  it("метка, текст и дополнение", () => {
    render(
      <ListRow lead="4" trailing="сейчас" accentLead>
        открытых лобби
      </ListRow>,
    );
    expect(screen.getByText("4").className).toContain("text-music");
    expect(screen.getByText("открытых лобби")).toBeInTheDocument();
    expect(screen.getByText("сейчас")).toBeInTheDocument();
  });

  it("без метки её нет", () => {
    render(<ListRow>Просто текст</ListRow>);
    expect(screen.queryByText("4")).toBeNull();
  });
});

describe("Cover", () => {
  it("с src — картинка с alt, без — заглушка без img", () => {
    const { rerender } = render(<Cover src="/a.jpg" alt="Кадр дня" />);
    expect(screen.getByRole("img", { name: "Кадр дня" })).toHaveAttribute("src", "/a.jpg");
    rerender(<Cover placeholder={<b>нота</b>} />);
    expect(screen.queryByRole("img")).toBeNull();
    expect(screen.getByText("нота")).toBeInTheDocument();
  });

  it("форма по ratio, чип в углу", () => {
    render(<Cover ratio="poster" label="Нолан целиком" />);
    expect(screen.getByTestId("cover").className).toContain("aspect-[2/3]");
    expect(screen.getByText("Нолан целиком")).toBeInTheDocument();
  });
});

describe("SectionCap", () => {
  it("капитель; accent — янтарная", () => {
    render(
      <>
        <SectionCap>Лента дейлика</SectionCap>
        <SectionCap accent>Событие</SectionCap>
      </>,
    );
    expect(screen.getByText("Лента дейлика").className).toContain("uppercase");
    expect(screen.getByText("Событие").className).toContain("text-music");
  });
});

describe("TierBadge", () => {
  it.each(TIER_KEYS)("звание %s: свой цвет заливки", (tier) => {
    render(<TierBadge tier={tier}>Звание</TierBadge>);
    const el = screen.getByText("Звание");
    expect(el).toHaveAttribute("data-tier", tier);
    expect(el.className).toContain(`bg-tier-${tier}`);
  });

  it("семейства rating сопоставляются со значками, незнакомое — сталь", () => {
    expect(tierKeyOf("iron")).toBe("steel");
    expect(tierKeyOf("gold")).toBe("gold");
    expect(tierKeyOf("melomaniac")).toBe("diamond");
    expect(tierKeyOf("что-то новое")).toBe("steel");
    expect(tierKeyOf(null)).toBe("steel");
  });
});

describe("DailyBadge", () => {
  it("исход по доле очков", () => {
    expect(dailyOutcome(5, 5)).toBe("exact");
    expect(dailyOutcome(4, 5)).toBe("exact");
    expect(dailyOutcome(3, 5)).toBe("partial");
    expect(dailyOutcome(2, 5)).toBe("partial");
    expect(dailyOutcome(1, 5)).toBe("miss");
    expect(dailyOutcome(0, 5)).toBe("miss");
    expect(dailyOutcome(null, 5)).toBe("skip");
  });

  it("музыка «♪ 5», кино «▣ 4», нет результата «—»", () => {
    const { container } = render(
      <>
        <DailyBadge domain="music" value={5} />
        <DailyBadge domain="film" value={4} />
        <DailyBadge domain="film" value={null} />
      </>,
    );
    const badges = [...container.querySelectorAll("[data-outcome]")];
    expect(badges.map((b) => b.textContent)).toEqual(["♪5", "▣4", "▣—"]);
    expect(badges.map((b) => b.getAttribute("data-outcome"))).toEqual(["exact", "exact", "skip"]);
  });
});
