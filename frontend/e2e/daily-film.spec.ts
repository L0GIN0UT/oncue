import { answerWithSuggestion, expect, test, openMenuItem } from "./fixtures";

test("«Кадр дня»: кадр сменяется с каждой попыткой, ревил — чёткий кадр, год и режиссёр", async ({
  page,
}) => {
  // Кино — второй дейлик, открывается из меню (/?domain=film), игра рисуется на месте.
  await page.goto("/");
  await openMenuItem(page, "nav-today-film");
  await expect(page).toHaveURL(/\/\?domain=film$/);
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
