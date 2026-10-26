/**
 * Сезонное событие на моках (этап 5d): тема `winter` и баннер, страница события (найденная,
 * открытая, закрытая находки, финал), спрятанный элемент, архив в профиле. Против живых сервисов
 * — шаг 0b.
 */
import { expect, login, test, openMenuItem } from "./fixtures";

test("баннер и тема winter: тема в первом HTML, ссылка в шапке и карточка на главной", async ({
  page,
  request,
  scenario,
}) => {
  await scenario({ event: "active" });
  // Ключ темы известен на сервере: data-theme есть уже в ответе, без мигания.
  const html = await (await request.get("/")).text();
  expect(html).toMatch(/<html[^>]*data-theme="winter"/);

  await page.goto("/");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "winter");
  await expect(page.getByTestId("nav-event")).toHaveAttribute("href", "/events/winter");
  // Аноним: прогресса нет.
  await expect(page.getByTestId("nav-event")).not.toContainText("Найдено");

  await login(page);
  await expect(page.getByTestId("nav-event")).toContainText("Найдено 1 из 4");
  await openMenuItem(page, "nav-solo");
  // Тема не отключается пользователем и переживает переходы.
  await expect(page.locator("html")).toHaveAttribute("data-theme", "winter");
});

test("без события: тема default, баннера нет", async ({ page }) => {
  await page.goto("/");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "default");
  await expect(page.getByTestId("nav-event")).toHaveCount(0);
  await expect(page.getByTestId("home-event")).toHaveCount(0);
});

test("страница события: карта, дневник, закрытая находка не кликабельна", async ({
  page,
  scenario,
  expectAccessible,
}) => {
  await scenario({ event: "active" });
  await login(page);
  await page.getByTestId("nav-event").click();
  await expect(page).toHaveURL(/\/events\/winter$/);

  await expect(page.getByTestId("event-progress")).toHaveText("Найдено 1 из 4");
  await expect(page.getByTestId("event-point-lantern")).toHaveAttribute("data-state", "found");
  await expect(page.getByTestId("event-point-snowman")).toHaveAttribute("data-state", "open");
  await expect(page.getByTestId("event-point-star")).toBeDisabled();
  await expect(page.getByTestId("event-diary-star")).toContainText("Пока скрыто");
  await expect(page.getByTestId("event-diary-lantern")).toContainText("тайнику с вареньем");

  await page.getByTestId("event-point-duelist").click();
  await expect(page.getByTestId("event-find-progress")).toContainText("1 из 3");
  await page.getByTestId("event-play").click();
  await expect(page).toHaveURL(/\/online$/);

  await page.goto("/events/winter");
  await expectAccessible(page);
});

test("финал: все находки найдены - награда получена, прогресс полный", async ({
  page,
  scenario,
}) => {
  await scenario({ event: "active", eventAllFound: true });
  await login(page);
  await page.goto("/events/winter");
  await expect(page.getByTestId("event-progress")).toHaveText("Найдено 4 из 4");
  await expect(page.getByTestId("event-point-star")).toHaveAttribute("data-state", "found");
  await expect(page.getByTestId("event-reward")).toContainText("Награда получена");
});

test("неизвестное событие - понятное сообщение", async ({ page, scenario }) => {
  await scenario({ event: "active" });
  await login(page);
  await page.goto("/events/ghost");
  await expect(page.getByTestId("event-error")).toContainText("Событие не найдено");
});

test("спрятанный элемент: виден только на своей странице, клик засчитывает находку", async ({
  page,
  scenario,
}) => {
  await scenario({ event: "active" });
  await login(page);

  await page.goto("/leaderboards");
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  await expect(page.getByTestId("event-spot")).toHaveCount(0);

  await page.goto("/shop");
  const spot = page.getByTestId("event-spot");
  await expect(spot).toBeVisible();
  await expect(spot).toHaveAccessibleName("Что-то блестит — присмотреться");
  await spot.click();
  await expect(page.getByText("Находка открыта!")).toBeVisible();
  await expect(spot).toHaveCount(0);
  await expect(page.getByTestId("nav-event")).toContainText("Найдено 2 из 4");

  await page.goto("/events/winter");
  await expect(page.getByTestId("event-point-snowman")).toHaveAttribute("data-state", "found");
});

test("архив событий в профиле: прошлые события со ссылками", async ({ page, scenario }) => {
  await scenario({ event: "finished", eventArchive: true });
  await login(page);
  await page.goto("/profile");
  const archive = page.getByTestId("events-archive");
  await expect(archive).toBeVisible();
  await expect(page.getByTestId("events-archive-autumn")).toContainText("Пройдено");
  await archive.getByRole("link", { name: "Зимняя охота" }).click();
  await expect(page).toHaveURL(/\/events\/winter$/);
  await expect(page.getByTestId("event-period")).toContainText("Событие завершено");
  // Завершённое событие: спрятанные элементы больше не рисуются.
  await page.goto("/shop");
  await expect(page.getByTestId("event-spot")).toHaveCount(0);
});
