import { expect, isMove, pickButton, playUntilResults, test } from "./fixtures";

test("больше/меньше: метрика загаданной скрыта до ответа, клавиатура, серия на итоге", async ({
  page,
  expectAccessible,
}) => {
  await page.goto("/play/film.pair.rating");
  await expect(page).toHaveURL(/\/play\/s\//);
  const sessionId = page.url().split("/").pop()!;

  await expect(page.getByTestId("shown-metric")).toHaveText(/\d/);
  await expect(page.getByTestId("streak")).toContainText("0");
  await expect(page.getByTestId("pick-option-higher")).toHaveText(/Больше/);
  await expect(page.getByTestId("pick-option-lower")).toHaveText(/Меньше/);
  await expect(page.getByTestId("reveal-metric")).toHaveCount(0);
  await expectAccessible(page);

  // Клавиша «1» — первый вариант («Больше»).
  const [move] = await Promise.all([
    page.waitForResponse((r) => isMove(r.url(), r.request().method())),
    page.keyboard.press("1"),
  ]);
  const body = (await move.json()) as { round: { reveal: { metric: number } | null } };
  expect(body.round.reveal?.metric).toEqual(expect.any(Number));
  await expect(page.getByTestId("reveal-metric")).toHaveText(/\d/);
  await expectAccessible(page);

  await playUntilResults(page, pickButton(page));
  await expect(page).toHaveURL(new RegExp(`/results/${sessionId}`));
  await expect(page.getByTestId("final-score")).toHaveText(/Серия\s*\d+/);
  await expectAccessible(page);
});
