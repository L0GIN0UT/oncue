import { answerWithSuggestion, expect, test } from "./fixtures";

test("«Кадр дня»: кадр сменяется с каждой попыткой, ревил — чёткий кадр, год и режиссёр", async ({
  page,
}) => {
  // Кино — второй дейлик: плитка на главной ведёт в игру.
  await page.goto("/");
  await page.getByTestId("home-daily-film").click();
  await expect(page).toHaveURL(/\/play\/s\//);
  const frame = page.getByTestId("clue-image");
  await expect(frame).toHaveAttribute("alt", "Кадр из фильма, подсказка 1");
  const first = await frame.getAttribute("src");
  await expect(page.getByTestId("skip")).toHaveText("Пропустить");

  await answerWithSuggestion(page, "Сол", "Солярис");
  await expect(frame).toHaveAttribute("alt", "Кадр из фильма, подсказка 2");
  expect(await frame.getAttribute("src")).not.toBe(first);

  await answerWithSuggestion(page, "Бра", "Брат");
  const reveal = page.getByTestId("reveal");
  await expect(reveal).toContainText("Угадал с 2-й");
  await expect(reveal.getByTestId("reveal-image")).toBeVisible();
  await expect(reveal).toContainText("1997");
  await expect(reveal).toContainText("Алексей Балабанов");
});
