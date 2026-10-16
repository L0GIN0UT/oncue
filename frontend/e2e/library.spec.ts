/**
 * Медиатека на моках (этап 3b, D6, D11): системные полки, своя полка (создание,
 * переименование, удаление), единица на полке и отметка `unavailable`; гостю — регистрация.
 */
import { apiPut, expect, login, test } from "./fixtures";

const FILM = "00000000-0000-4000-8000-000000000201";
const BLOCKED = "00000000-0000-4000-8000-000000000202";

test("полки: системные и своя, единица на полке, заблокированная — unavailable", async ({
  page,
  scenario,
  expectAccessible,
}) => {
  await login(page);
  // Обе единицы сохранены, затем одну блокируют в каталоге: она остаётся на полке (D11).
  for (const id of [FILM, BLOCKED]) {
    const res = await apiPut(page, `/api/library/v1/shelves/system:watched/items/${id}`, {
      source: "reveal",
    });
    expect(res.status()).toBe(200);
  }
  await scenario({ ugc: { blockedItem: BLOCKED } });

  await page.goto("/library");
  await expect(page.getByTestId("shelf-card-system:watch_later")).toBeVisible();
  const watched = page.getByTestId("shelf-card-system:watched");
  await expect(watched).toContainText("2");
  await expectAccessible(page);
  await watched.click();
  await expect(page).toHaveURL(/\/library\/system:watched$/);
  await expect(page.getByTestId(`library-item-${FILM}`)).toBeVisible();
  await expect(page.getByTestId(`library-item-unavailable-${BLOCKED}`)).toHaveText(
    "Недоступно в каталоге",
  );
  await expectAccessible(page);

  // Своя полка: создать → переименовать → удалить.
  await page.goto("/library");
  await page.getByTestId("shelf-new-title").fill("Ужасы на выходные");
  await page.getByTestId("shelf-create").click();
  const custom = page
    .locator("[data-testid^=shelf-card-]")
    .filter({ hasText: "Ужасы на выходные" });
  await expect(custom).toBeVisible();
  await page.getByRole("button", { name: "Переименовать" }).click();
  await page.getByLabel("Переименовать").fill("Хорроры");
  await page.getByRole("button", { name: "Готово" }).click();
  await expect(
    page.locator("[data-testid^=shelf-card-]").filter({ hasText: "Хорроры" }),
  ).toBeVisible();
  page.once("dialog", (d) => void d.accept());
  await page.locator("[data-testid^=shelf-delete-]").click();
  await expect(page.locator("[data-testid^=shelf-card-]")).toHaveCount(3);
});

test("гость: медиатека предлагает регистрацию, ссылки «Медиатека» в шапке нет", async ({
  page,
}) => {
  await page.goto("/");
  await expect(page.getByTestId("nav-collections")).toBeVisible();
  await expect(page.getByTestId("nav-library")).toHaveCount(0);
  await page.goto("/library");
  await expect(
    page.getByRole("heading", { name: "Медиатека — для игроков с аккаунтом" }),
  ).toBeVisible();
});
