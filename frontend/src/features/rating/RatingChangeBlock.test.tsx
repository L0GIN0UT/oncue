import { describe, expect, it } from "vitest";
import { http, HttpResponse } from "msw";
import { screen } from "@testing-library/react";
import { server, setupMockBackend } from "@/test/msw";
import { renderWithProviders } from "@/test/render";
import { RatingChangeBlock } from "./RatingChangeBlock";

setupMockBackend();

const ME = "00000000-0000-4000-8000-00000000000a";
const MATCH = "00000000-0000-4000-8000-0000000000aa";
const URL = "*/api/rating/v1/matches/:id/changes";

const tier = (family: string, step: number | null, title = `rating.tier.${family}`) => ({
  family,
  key: step ? `${family}_${step}` : family,
  step,
  title_key: title,
});

const change = (patch: Record<string, unknown> = {}) => ({
  user_id: ME,
  ladder: "film",
  format: "classic",
  outcome: "win",
  old: 1195,
  new: 1213,
  delta: 18,
  old_tier: tier("bronze", 3),
  tier: tier("silver", 1),
  calibrating: false,
  calibration_left: 0,
  leaver_penalty: 0,
  ...patch,
});

describe("RatingChangeBlock — дельта рейтинга в итоге", () => {
  it("показывает дельту, старое и новое значение и новое звание", async () => {
    server.use(http.get(URL, () => HttpResponse.json({ match_id: MATCH, items: [change()] })));
    renderWithProviders(<RatingChangeBlock matchId={MATCH} myId={ME} />);
    expect(screen.getByTestId("rating-change-loading")).toBeInTheDocument();
    expect(await screen.findByTestId("rating-delta")).toHaveTextContent("+18");
    expect(screen.getByText("1195 → 1213")).toBeInTheDocument();
    expect(screen.getByTestId("rating-tier")).toHaveTextContent("Новое звание: Серебро 1");
    expect(screen.queryByTestId("rating-penalty")).not.toBeInTheDocument();
  });

  it("опрашивает: 404 до обработки события, затем данные", async () => {
    let calls = 0;
    server.use(
      http.get(URL, () => {
        calls += 1;
        return calls < 2
          ? HttpResponse.json(
              { code: "rating.match_not_rated", status: 404, title: "x", type: "x" },
              { status: 404 },
            )
          : HttpResponse.json({ match_id: MATCH, items: [change({ delta: -7, new: 1188 })] });
      }),
    );
    renderWithProviders(<RatingChangeBlock matchId={MATCH} myId={ME} />);
    expect(await screen.findByTestId("rating-delta", {}, { timeout: 4000 })).toHaveTextContent(
      "−7",
    );
    expect(calls).toBe(2);
  });

  it("калибровка: числа скрыты, вместо звания — «осталось N матчей»; штраф за выход виден", async () => {
    server.use(
      http.get(URL, () =>
        HttpResponse.json({
          match_id: MATCH,
          items: [
            change({
              calibrating: true,
              calibration_left: 4,
              old: null,
              new: null,
              delta: null,
              old_tier: null,
              tier: null,
              leaver_penalty: 10,
            }),
          ],
        }),
      ),
    );
    renderWithProviders(<RatingChangeBlock matchId={MATCH} myId={ME} />);
    expect(await screen.findByTestId("calibration")).toHaveTextContent("осталось 4 матча");
    expect(screen.queryByTestId("rating-delta")).not.toBeInTheDocument();
    expect(screen.getByTestId("rating-penalty")).toHaveTextContent("−10");
  });

  it("высшее звание без ступени", async () => {
    server.use(
      http.get(URL, () =>
        HttpResponse.json({
          match_id: MATCH,
          items: [
            change({
              old_tier: tier("cinephile", null, "rating.tier.cinephile"),
              tier: tier("cinephile", null, "rating.tier.cinephile"),
            }),
          ],
        }),
      ),
    );
    renderWithProviders(<RatingChangeBlock matchId={MATCH} myId={ME} />);
    expect(await screen.findByTestId("rating-tier")).toHaveTextContent("Звание: Киноман");
  });

  it("ошибка rating не ломает итог: спокойная строка «появится позже»", async () => {
    server.use(
      http.get(URL, () =>
        HttpResponse.json(
          { code: "rating.unavailable", status: 503, title: "x", type: "x" },
          { status: 503 },
        ),
      ),
    );
    renderWithProviders(<RatingChangeBlock matchId={MATCH} myId={ME} />);
    expect(await screen.findByTestId("rating-change-later")).toBeInTheDocument();
  });
});
