/**
 * Сборники на моках (этап 3b, D3, D5, D7): каталог с поиском из адреса, страница сборника,
 * лайк и избранное, игра 5 раундов, «Сохранить» из ревила и итога (exclusive_with), «пройден»
 * открывает состав, гостю — регистрация. Селекторы — контракт `../e2e/collections.spec.ts`.
 */
import { expect, giveUpButton, isMove, login, playUntilResults, test } from "./fixtures";

const CURATED = "00000000-0000-4000-a000-000000000001";
const UGC = "00000000-0000-4000-a000-000000000002";
const HIDDEN = "00000000-0000-4000-a000-000000000004";
const FRAME = "film.collection.frame";

test("каталог: витрины, поиск и фильтры из адреса, карточка ведёт на страницу", async ({
  page,
  expectAccessible,
}) => {
  await page.goto("/collections");
  await expect(page.getByTestId("collections-block-featured")).toContainText(
    "Кино · кадры: классика",
  );
  await expectAccessible(page);

  await page.goto("/collections?domain=film&sort=new");
  await expect(page.getByTestId(`collection-card-${CURATED}`)).toBeVisible();
  await page.getByTestId("collection-search").fill("тарков");
  await expect(page).toHaveURL(/[?&]q=.+/);
  await expect(page.getByTestId(`collection-card-${CURATED}`)).toHaveCount(0);
  const card = page.getByTestId(`collection-card-${UGC}`);
  await expect(card).toBeVisible();
  await card.click();
  await expect(page).toHaveURL(new RegExp(`/collections/${UGC}$`));
  await expect(page.getByTestId("collection-author")).toHaveText("author");
  await expect(page.getByTestId("collection-items-count")).toHaveText("5");
  await expect(page.getByTestId("collection-items")).toHaveCount(0);
  await expectAccessible(page);
});

test("игрок: лайк и избранное, 5 раундов, сохранение из ревила и итога, «пройден»", async ({
  page,
  expectAccessible,
}) => {
  test.setTimeout(90_000);
  await login(page);
  await page.goto(`/collections/${CURATED}`);
  await expect(page.getByTestId(`collection-mode-${FRAME}`)).toContainText(/8\D+8/);
  await expect(page.getByTestId("collection-items")).toHaveCount(0);

  const like = page.getByTestId("collection-like");
  await like.click();
  await expect(like).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByTestId("collection-likes-count")).toHaveText("1");
  await page.getByTestId("collection-favorite").click();
  await expect(page.getByTestId("collection-favorite")).toHaveAttribute("aria-pressed", "true");
  await page.reload();
  await expect(page.getByTestId("collection-like")).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByTestId("collection-favorite")).toHaveAttribute("aria-pressed", "true");
  await expectAccessible(page);

  await page.getByTestId(`collection-mode-${FRAME}`).click();
  await page.getByTestId("collection-rounds-5").click();
  await page.getByTestId("collection-play").click();
  await expect(page).toHaveURL(/\/play\/s\//);
  // Режим-шаблон найден в `GET /v1/modes?kind=collection`: название и лента уровней из конфига.
  await expect(page.getByText("Сборник по кадрам", { exact: true })).toBeVisible();
  await expect(page.getByRole("list", { name: "Подсказки: открыт уровень 1 из 6" })).toBeVisible();

  // Ревил: «Сохранить → Смотреть позже».
  const [move] = await Promise.all([
    page.waitForResponse((r) => isMove(r.url(), r.request().method())),
    giveUpButton(page).click(),
  ]);
  const itemId = ((await move.json()) as { round: { reveal: { media_item_id: string } } }).round
    .reveal.media_item_id;
  const reveal = page.getByTestId("reveal");
  await reveal.getByTestId("save-button").click();
  const later = page.getByTestId("shelf-option-system:watch_later");
  await Promise.all([
    page.waitForResponse(
      (r) =>
        r.request().method() === "PUT" && r.url().endsWith(`/system:watch_later/items/${itemId}`),
    ),
    later.click(),
  ]);
  await expect(later).toHaveAttribute("aria-checked", "true");
  await expectAccessible(page);
  await page.keyboard.press("Escape");
  await expect(page.getByTestId("shelf-picker")).toHaveCount(0);

  await playUntilResults(page, giveUpButton(page));
  await expect(page).toHaveURL(/\/results\//);
  await expect(page.getByTestId("result-row")).toHaveCount(5);
  await expect(page.getByTestId("results-collection")).toContainText("Кино · кадры: классика");

  // Итог: «Просмотрено» снимает «Смотреть позже» сразу (exclusive_with).
  await page.getByTestId("result-row").first().getByTestId("save-button").click();
  await page.getByTestId("shelf-option-system:watched").click();
  await expect(page.getByTestId("shelf-option-system:watched")).toHaveAttribute(
    "aria-checked",
    "true",
  );
  await expect(page.getByTestId("shelf-option-system:watch_later")).toHaveAttribute(
    "aria-checked",
    "false",
  );
  await page.keyboard.press("Escape");
  await expectAccessible(page);

  // «Пройден» открывает состав.
  await page.goto(`/collections/${CURATED}`);
  await expect(page.getByTestId("collection-completed")).toBeVisible();
  await expect(
    page.getByTestId("collection-items").locator("[data-testid^=collection-entry-]"),
  ).toHaveCount(8);
  await expect(page.getByTestId(`collection-entry-${itemId}`)).toBeVisible();
});

test("гость: играет сборник, лайк и «Сохранить» ведут на регистрацию", async ({ page }) => {
  await page.goto(`/collections/${CURATED}`);
  await page.getByTestId(`collection-mode-${FRAME}`).click();
  await page.getByTestId("collection-rounds-5").click();
  await page.getByTestId("collection-play").click();
  await expect(page).toHaveURL(/\/play\/s\//);
  await giveUpButton(page).click();
  await expect(page.getByTestId("reveal").getByTestId("save-button")).toHaveAttribute(
    "href",
    /\/register/,
  );
  await page.goto(`/collections/${CURATED}`);
  await page.getByTestId("collection-like").click();
  await expect(page).toHaveURL(/\/register/);
});

test("мои сборники: плашка «скрыт модератором», шапка ведёт на сборники и медиатеку", async ({
  page,
  expectAccessible,
}) => {
  await login(page);
  await page.getByTestId("user-name").click();
  await expect(page).toHaveURL(/\/me\/collections$/);
  await expect(
    page.getByTestId(`my-collection-${HIDDEN}`).getByTestId("my-collection-hidden"),
  ).toBeVisible();
  await expectAccessible(page);
  await page.getByTestId("nav-collections").click();
  await expect(page).toHaveURL(/\/collections$/);
  await page.getByTestId("nav-library").click();
  await expect(page).toHaveURL(/\/library$/);
});
