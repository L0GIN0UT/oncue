import { expect, giveUpButton, playUntilResults, test } from "./fixtures";

test("марафон: выбор режиссёра, три жизни, итог по сыгранным раундам", async ({
  page,
  expectAccessible,
}) => {
  const searches: string[] = [];
  page.on("request", (r) => {
    const url = new URL(r.url());
    if (url.pathname.endsWith("/subjects")) searches.push(url.searchParams.get("q") ?? "");
  });
  await page.goto("/solo");
  await page
    .getByTestId("mode-card-film.marathon.director")
    .getByRole("link", { name: "Выбрать персону" })
    .click();
  const picker = page.getByTestId("subject-picker");
  await expect(picker).toBeVisible();
  await expect(page.getByTestId("subject-start")).toBeDisabled();
  await expectAccessible(page);

  await picker.getByTestId("subject-search").pressSequentially("Тарков", { delay: 30 });
  const option = page.locator("[data-testid^=subject-option-]", { hasText: "Андрей Тарковский" });
  await expect(option).toBeVisible();
  // Пауза ввода 200 мс: на быстрый набор — один запрос с полным текстом.
  await expect.poll(() => searches.filter(Boolean)).toEqual(["Тарков"]);
  await option.click();
  await expect(option).toHaveAttribute("aria-pressed", "true");
  await page.getByTestId("subject-start").click();
  await expect(page).toHaveURL(/\/play\/s\//);
  const sessionId = page.url().split("/").pop()!;

  await expect(page.getByTestId("lives")).toContainText("3");
  await expectAccessible(page);
  await playUntilResults(page, giveUpButton(page));
  await expect(page).toHaveURL(new RegExp(`/results/${sessionId}`));
  // Три сдачи — три потерянные жизни: ровно три сыгранных раунда из пяти.
  await expect(page.getByTestId("result-row")).toHaveCount(3);
  await expectAccessible(page);
});
