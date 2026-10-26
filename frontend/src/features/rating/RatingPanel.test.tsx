import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { screen, within } from "@testing-library/react";
import { renderWithProviders } from "@/test/render";
import { RatingPanel } from "./RatingPanel";
import type { RatingCard, SeasonCard } from "./labels";

const bronze2 = { family: "bronze", key: "bronze_2", step: 2, title_key: "rating.tier.bronze" };
const cinephile = {
  family: "cinephile",
  key: "cinephile",
  step: null,
  title_key: "rating.tier.cinephile",
};

const rating = (patch: Partial<RatingCard>): RatingCard => ({
  ladder: "film",
  rating: 1120,
  tier: bronze2,
  next_tier_at: 1200,
  calibrating: false,
  calibration_left: 0,
  matches_played: 14,
  place: 42,
  ...patch,
});

const season: SeasonCard = {
  ladder: "film",
  number: 3,
  starts_at: "2026-10-01T00:00:00Z",
  ends_at: "2026-11-01T00:00:00Z",
};

describe("RatingPanel — звание, калибровка, сезон", () => {
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-10-20T12:00:00Z"));
  });
  afterEach(() => vi.useRealTimers());

  it("звание со ступенью, очки, место, прогресс до следующего звания и сезон", () => {
    renderWithProviders(<RatingPanel ratings={[rating({})]} seasons={[season]} />);
    const card = screen.getByTestId("rating-film");
    expect(within(card).getByTestId("tier")).toHaveTextContent("Бронза 2");
    expect(card).toHaveTextContent("1120");
    expect(card).toHaveTextContent("Место 42");
    expect(within(card).getByTestId("next-tier")).toHaveTextContent(
      "До следующего звания: 80 очков",
    );
    expect(within(card).getByTestId("season-film")).toHaveTextContent("Сезон 3 · осталось 12 дней");
  });

  it("в калибровке звание скрыто: вместо него «осталось N матчей»", () => {
    renderWithProviders(
      <RatingPanel
        ratings={[
          rating({ rating: null, tier: null, calibrating: true, calibration_left: 7, place: null }),
        ]}
        seasons={[season]}
      />,
    );
    const card = screen.getByTestId("rating-film");
    expect(within(card).getByTestId("calibration")).toHaveTextContent(
      "Калибровка: осталось 7 матчей",
    );
    expect(within(card).queryByTestId("tier")).not.toBeInTheDocument();
    expect(card).not.toHaveTextContent("очк.");
  });

  it("высшее звание — без ступени и без прогресса", () => {
    renderWithProviders(
      <RatingPanel
        ratings={[rating({ ladder: "music", tier: cinephile, rating: 2500, next_tier_at: null })]}
        seasons={null}
      />,
    );
    const card = screen.getByTestId("rating-music");
    expect(within(card).getByTestId("tier")).toHaveTextContent(/^Киноман$/);
    expect(within(card).getByTestId("next-tier")).toHaveTextContent("Высшее звание");
    // Сезонов нет (блок недоступен) — строки сезона нет, карточка цела.
    expect(within(card).queryByTestId("season-music")).not.toBeInTheDocument();
  });

  it("неизвестное звание не ломает экран: запасной текст — семейство", () => {
    renderWithProviders(
      <RatingPanel
        ratings={[
          rating({
            tier: { family: "mythic", key: "mythic_1", step: 1, title_key: "rating.tier.mythic" },
          }),
        ]}
        seasons={null}
      />,
    );
    expect(screen.getByTestId("tier")).toHaveTextContent("Звание 1");
  });

  it("блок недоступен / пусто", () => {
    const { unmount } = renderWithProviders(<RatingPanel ratings={null} seasons={null} />);
    expect(screen.getByTestId("ratings-unavailable")).toBeInTheDocument();
    unmount();
    renderWithProviders(<RatingPanel ratings={[]} seasons={null} />);
    expect(screen.getByText(/после первого рейтингового матча/)).toBeInTheDocument();
  });
});
