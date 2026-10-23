/**
 * Полный стек: рейтинговый подбор 4b (план docs/plans/stage-4.md §0 п. 9; ADR 0037, 0038).
 *
 * Два игрока встают в очередь «Кино·Классика» (ladder `film`, формат `classic`) через API match,
 * матчер создаёт рейтинговую дуэль, оба попадают в комнату, B сдаётся — для рейтинга это
 * поражение с `leaver_penalty`. Затем rating применяет `MatchFinished`: у обоих запись в
 * истории, в калибровке числа скрыты (`null`), виден счётчик «осталось N матчей», у сдавшегося
 * в дельте матча — штраф.
 *
 * Спека на уровне API: экранов очереди, звания и дельты в итоге во фронтенде пока нет (поток D2
 * этапа 4b, см. docs/handoff/stage-4b.md). Когда D2 будет готов, шаги входа в очередь и проверки
 * итога переносятся на UI (`data-testid` очереди и дельты), API-проверки остаются как контрактные.
 *
 * Ladder-ы и шаблоны `*.ranked.*` приходят из засева (`seeds/rating/ladders`, `seeds/game/modes`).
 * Пользователи — свежие на прогон (`e2e/online.ts`).
 */
import { expect, test } from "@playwright/test";
import {
  api,
  closePlayers,
  forfeit,
  json,
  problem,
  registerPlayers,
  type Player,
} from "./online";

test.skip(process.env.E2E_SEEDED !== "1", "стек не засеян: нужен шаг засева (E2E_SEEDED=1)");

const LADDER = "film";
const FORMAT = "classic";
// Калибровка из seeds/rating/ladders/film.yaml: calibration.matches = 10.
const CALIBRATION_MATCHES = 10;

type Ticket = { state: "searching" | "matched"; room_code: string | null; ladder: string; format: string };
type RatingView = { ladder: string; rating: number | null; tier: unknown; calibrating: boolean; calibration_left: number; matches_played: number };
type HistoryPage = { items: { match_id: string; outcome: string; old: number | null; new: number | null; delta: number | null }[] };
type Changes = { items: { outcome: string; leaver_penalty: number; calibrating: boolean; calibration_left: number; delta: number | null }[] };

let players: Player[] = [];
test.afterAll(async () => closePlayers(players));

test("рейтинговая дуэль: очередь → матч → сдача → рейтинг применён (калибровка)", async ({ browser }) => {
  test.setTimeout(300_000);
  players = await test.step("регистрация A и B", () => registerPlayers(browser, ["e2eRa", "e2eRb"]));
  const [a, b] = players as [Player, Player];

  await test.step("ladder `film` опубликован rating и виден в списке", async () => {
    const res = await json<{ items: { key: string; formats: string[] }[] }>(await api(a.page, "rating").get("/ladders"));
    expect(res.items.find((l) => l.key === LADDER)?.formats).toEqual(expect.arrayContaining([FORMAT]));
  });

  await test.step("A и B встают в очередь «Кино·Классика»", async () => {
    for (const p of players) {
      const ticket = await json<Ticket>(await api(p.page, "match").post("/queue/enter", { ladder: LADDER, format: FORMAT }));
      expect([ticket.ladder, ticket.format]).toEqual([LADDER, FORMAT]);
    }
    // Игрок состоит ровно в одной очереди: другой формат того же ladder-а — 409.
    const other = await problem(await api(a.page, "match").post("/queue/enter", { ladder: LADDER, format: "lite" }), 409);
    expect(other.code).toBe("match.active_room_conflict");
  });

  const code = await test.step("матчер собирает пару: оба получают одну комнату", async () => {
    const codes: string[] = [];
    for (const p of players) {
      await expect
        .poll(
          async () => {
            const ticket = await json<Ticket>(await api(p.page, "match").post("/queue/tick"));
            return ticket.state === "matched" ? ticket.room_code : null;
          },
          { timeout: 60_000, intervals: [1_000] },
        )
        .not.toBeNull();
      const ticket = await json<Ticket>(await api(p.page, "match").post("/queue/tick"));
      codes.push(ticket.room_code!);
    }
    expect(codes[0]).toBe(codes[1]);
    return codes[0]!;
  });

  await test.step("оба в рейтинговой комнате: отсчёт → раунд 1", async () => {
    for (const p of players) await p.page.goto(`/room/${code}`);
    for (const p of players) {
      await expect(p.page.getByTestId("match-round")).toContainText("Раунд 1", { timeout: 60_000 });
    }
    // В идущем матче элемента `room-code` нет (он только в лобби) — код берём из адреса.
    await expect(a.page).toHaveURL(`/room/${code}`);
  });

  await test.step("B сдаётся → итог: поражение у B", async () => {
    await forfeit(b.page);
    await expect(a.page.getByTestId("summary-headline")).toHaveText("Соперник сдался");
    await expect(b.page.getByTestId("summary-headline")).toHaveText("Поражение");
  });

  const matchIds: string[] = [];
  await test.step("rating применил матч: запись в истории у обоих, числа в калибровке скрыты", async () => {
    for (const [p, expected] of [[a, "win"], [b, "loss"]] as const) {
      const history = await expect
        .poll(
          async () => (await json<HistoryPage>(await api(p.page, "rating").get(`/me/ratings/${LADDER}/history`))).items.length,
          { timeout: 60_000, intervals: [1_000] },
        )
        .toBe(1)
        .then(async () => json<HistoryPage>(await api(p.page, "rating").get(`/me/ratings/${LADDER}/history`)));
      const item = history.items[0]!;
      expect(item.outcome).toBe(expected);
      // Калибровка: рейтинг и дельта скрыты.
      expect([item.old, item.new, item.delta]).toEqual([null, null, null]);
      matchIds.push(item.match_id);
    }
    expect(new Set(matchIds).size).toBe(1);
  });

  await test.step("«осталось N матчей» и штраф за сдачу в дельте матча", async () => {
    for (const p of players) {
      const mine = await json<{ items: RatingView[] }>(await api(p.page, "rating").get("/me/ratings"));
      const view = mine.items.find((r) => r.ladder === LADDER)!;
      expect(view).toMatchObject({ rating: null, tier: null, calibrating: true, matches_played: 1 });
      expect(view.calibration_left).toBe(CALIBRATION_MATCHES - 1);
    }
    const loser = await json<Changes>(await api(b.page, "rating").get(`/matches/${matchIds[0]}/changes`));
    expect(loser.items.find((i) => i.outcome === "loss")?.leaver_penalty).toBeGreaterThan(0);
    const winner = await json<Changes>(await api(a.page, "rating").get(`/matches/${matchIds[0]}/changes`));
    expect(winner.items.find((i) => i.outcome === "win")?.leaver_penalty).toBe(0);
  });
});
