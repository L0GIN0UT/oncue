/**
 * Social на моках (этап 5c): друзья и заявки, блокировки и приватность, профиль игрока
 * (`restricted`), приглашение в комнату и «Войти» из уведомления, комментарии сборника, жалобы,
 * санкции (баннер, страница, онлайн-бан, бан аккаунта при входе) и присутствие. Против живых
 * сервисов — `../e2e/friends.spec.ts` (шаг 0b). Мок — e2e/mocks/social.ts.
 */
import { expect, login, test } from "./fixtures";
import { createRoomFromHub, openHubTab } from "./hubs";

const MOCK_URL = `http://127.0.0.1:${process.env.E2E_MOCK_PORT ?? 3101}`;
const COMMENTED = "00000000-0000-4000-a000-000000000001";
const PLAYER_ID = "00000000-0000-4000-8000-000000000902";

test("друзья: статусы, заявка принимается, ник добавляется, удаление требует подтверждения", async ({
  page,
  expectAccessible,
}) => {
  await login(page);
  await page.goto("/friends");
  await expect(page.getByTestId("friend-Анна")).toHaveAttribute("data-status", "online");
  await expect(page.getByTestId("friend-Борис")).toHaveAttribute("data-status", "in_match");
  await expect(page.getByTestId("friend-Клара")).toHaveAttribute("data-status", "offline");
  await expectAccessible(page);

  await page.getByTestId("friends-tab-requests").click();
  await page.getByTestId("request-in-Дмитрий").getByTestId("request-accept").click();
  await expect(page.getByTestId("request-in-Дмитрий")).toHaveCount(0);
  await page.getByTestId("friends-tab-friends").click();
  await expect(page.getByTestId("friend-Дмитрий")).toBeVisible();

  await page.getByTestId("friend-Клара").getByTestId("friend-remove").click();
  await page.getByTestId("friend-Клара").getByTestId("friend-remove-confirm").click();
  await expect(page.getByTestId("friend-Клара")).toHaveCount(0);

  await page.getByTestId("friends-tab-add").click();
  await page.getByTestId("add-nickname").fill("Никто");
  await page.getByTestId("add-nickname-submit").click();
  await expect(page.getByTestId("social-error")).toContainText("Игрок не найден");
  await page.getByTestId("add-nickname").fill("Клара");
  await page.getByTestId("add-nickname-submit").click();
  await expect(page.getByTestId("add-sent")).toContainText("Клара");
  await expectAccessible(page);

  await page.getByTestId("friends-tab-requests").click();
  await expect(page.getByTestId("request-out-Клара")).toBeVisible();
  await page.getByTestId("request-out-Клара").getByTestId("request-cancel").click();
  await expect(page.getByTestId("request-out-Клара")).toHaveCount(0);
});

test("мой код перевыпускается; приватность и блокировки доступны", async ({
  page,
  expectAccessible,
}) => {
  await login(page);
  await page.goto("/friends?tab=add");
  await expect(page.getByTestId("my-code")).toHaveText("ABCD2345");
  await page.getByTestId("code-regenerate").click();
  await expect(page.getByTestId("my-code")).toHaveText("WXYZ6789");

  await page.getByTestId("friends-tab-privacy").click();
  await page.getByTestId("privacy-requests-none").click();
  await expect(page.getByTestId("privacy-requests-none")).toBeChecked();
  await page.reload();
  await page.getByTestId("friends-tab-privacy").click();
  await expect(page.getByTestId("privacy-requests-none")).toBeChecked();
  await expectAccessible(page);

  await page.getByTestId("friends-tab-blocks").click();
  await expect(page.getByTestId("blocks-empty")).toBeVisible();
});

test("профиль игрока: блокировка и снятие; закрытый профиль — только пояснение", async ({
  page,
  scenario,
  expectAccessible,
}) => {
  await login(page);
  await page.goto(`/players/${PLAYER_ID}`);
  await expect(page.getByTestId("player-level")).toBeVisible();
  await expectAccessible(page);
  await page.getByTestId("player-block").click();
  await page.getByTestId("player-block-confirm").click();
  // После блокировки профиль закрывается для зрителя (restricted), снять блокировку можно.
  await expect(page.getByTestId("player-restricted")).toContainText("Профиль недоступен");
  await expect(page.getByTestId("player-level")).toHaveCount(0);
  await page.getByTestId("player-unblock").click();
  await expect(page.getByTestId("player-level")).toBeVisible();

  await scenario({ social: { restrictedPlayer: PLAYER_ID } });
  await page.reload();
  await expect(page.getByTestId("player-restricted")).toBeVisible();
  await expect(page.getByTestId("player-badges")).toHaveCount(0);
  await expect(page.getByTestId("player-block")).toHaveCount(0);
});

test("приглашение: из лобби зовём друга онлайн; отказ 403 и лимит 429 объяснены", async ({
  page,
  scenario,
}) => {
  await login(page);
  await page.goto("/online");
  await openHubTab(page, /^Друзья/);
  await expect(page.getByTestId("hub-friend-Анна")).toContainText("В сети");
  await openHubTab(page, /^Лобби/);
  await createRoomFromHub(page, "music.duel.classic");
  await expect(page).toHaveURL(/\/room\/[A-HJ-NP-Z2-9]{8}$/);
  const panel = page.getByTestId("invite-panel");
  await expect(panel.getByTestId("invite-Анна")).toBeVisible();
  await expect(panel.getByTestId("invite-Борис")).toHaveCount(0);

  await scenario({ social: { inviteForbidden: true } });
  await panel.getByTestId("invite-send").click();
  await expect(panel.getByTestId("social-error")).toContainText("нельзя пригласить");

  await scenario({ social: { inviteRateLimited: true } });
  await panel.getByTestId("invite-send").click();
  await expect(panel.getByTestId("social-error")).toContainText("Слишком часто");

  await scenario({ social: {} });
  await panel.getByTestId("invite-send").click();
  await expect(panel.getByTestId("invite-sent")).toBeVisible();
});

test("уведомление room_invite: карточка у колокольчика и «Войти» в списке ведут в комнату", async ({
  page,
  request,
}) => {
  await login(page);
  await request.post(`${MOCK_URL}/api/notifications/__e2e/push`, {
    data: {
      type: "room_invite",
      title: "Приглашение в игру",
      body: "Анна зовёт вас в комнату",
      data: { room_code: "ABCD2345" },
    },
  });
  const card = page.getByTestId("room-invite-card");
  await expect(card).toContainText("Приглашение в игру");
  await page.goto("/notifications");
  await expect(page.getByTestId("notification-join").first()).toHaveAttribute(
    "href",
    "/room/ABCD2345",
  );
  await page.getByTestId("notification-join").first().click();
  await expect(page).toHaveURL(/\/room\/ABCD2345$/);
});

test("комментарии: страницы по курсору, публикация, правка, удаление, ошибки", async ({
  page,
  scenario,
  expectAccessible,
}) => {
  await login(page);
  await page.goto(`/collections/${COMMENTED}`);
  const comments = page.getByTestId("comment");
  await expect(comments).toHaveCount(2);
  await expect(page.getByTestId("comments-total")).toContainText("3 комментария");
  await page.getByTestId("comments-more").click();
  await expect(comments).toHaveCount(3);
  await expect(page.getByTestId("comments-more")).toHaveCount(0);
  await expectAccessible(page);

  await page.getByTestId("comment-text").fill("Отличный сборник");
  await expect(page.getByTestId("comment-counter")).toHaveText("16/500");
  await page.getByTestId("comment-submit").click();
  const mine = page.locator("[data-testid=comment][data-mine=true]");
  await expect(mine).toContainText("Отличный сборник");
  await mine.getByTestId("comment-edit").click();
  await mine.getByTestId("comment-edit-text").fill("Правка");
  await mine.getByRole("button", { name: "Сохранить" }).click();
  await expect(mine.getByTestId("comment-body")).toHaveText("Правка");
  await mine.getByTestId("comment-delete").click();
  await expect(mine).toHaveCount(0);

  for (const [code, text] of [
    ["blocked", "блокировки"],
    ["chat_muted", "временно нельзя"],
    ["comment_rejected", "не прошёл проверку"],
    ["comments_rate_limit", "Слишком много"],
  ] as const) {
    await scenario({ social: { commentError: code } });
    await page.getByTestId("comment-text").fill("Привет");
    await page.getByTestId("comment-submit").click();
    await expect(page.getByTestId("social-error")).toContainText(text);
  }
});

test("комментарии: гость читает и видит приглашение войти", async ({ page }) => {
  await page.request.post("/api/identity/v1/auth/guest");
  await page.goto(`/collections/${COMMENTED}`);
  await expect(page.getByTestId("comment")).toHaveCount(2);
  await expect(page.getByTestId("comments-login")).toContainText("Войдите");
  await expect(page.getByTestId("comment-text")).toHaveCount(0);
});

test("жалоба на сборник и на комментарий: причины по виду, повтор — «уже отправлена»", async ({
  page,
  expectAccessible,
}) => {
  await login(page);
  await page.goto(`/collections/${COMMENTED}`);
  await page.getByTestId("comment-report").first().click();
  const dialog = page.getByTestId("report-dialog");
  await expect(dialog).toBeVisible();
  await expect(dialog.getByTestId("report-reason-spam")).toBeVisible();
  await expect(dialog.getByTestId("report-reason-cheating")).toHaveCount(0);
  await expectAccessible(page);
  await dialog.getByTestId("report-reason-abuse").check();
  await dialog.getByTestId("report-text").fill("грубо");
  await dialog.getByTestId("report-submit").click();
  await expect(page.getByTestId("report-done").first()).toContainText("Жалоба отправлена");

  await page.getByTestId("collection-report").click();
  await page.getByTestId("report-reason-spam").check();
  await page.getByTestId("report-submit").click();
  await expect(page.getByTestId("report-done")).toHaveCount(2);
});

test("санкции: баннер в оболочке, страница, онлайн-бан в хабе", async ({
  page,
  scenario,
  expectAccessible,
}) => {
  await scenario({ social: { sanctions: ["online", "chat"], onlineBanned: true } });
  await login(page);
  await expect(page.getByTestId("sanctions-banner")).toContainText("Ограничения аккаунта");
  await page.getByTestId("sanctions-link").click();
  await expect(page).toHaveURL(/\/sanctions$/);
  await expect(page.getByTestId("sanction-online")).toContainText("До ");
  await expect(page.getByTestId("sanction-chat")).toContainText("Причина");
  await expectAccessible(page);

  await page.goto("/online");
  await expect(page.getByTestId("online-banned")).toBeVisible();
  await createRoomFromHub(page, "music.duel.classic");
  await expect(page.getByTestId("create-room-error")).toContainText("недоступны");
});

test("вход заблокированного аккаунта: сообщение без причины", async ({ page, scenario }) => {
  await scenario({ social: { accountBanned: true } });
  await page.goto("/login");
  await page.getByLabel("E-mail или ник").fill("player");
  await page.getByLabel("Пароль").fill("correct-horse");
  await page.getByRole("button", { name: "Войти" }).click();
  await expect(page.getByText("Аккаунт заблокирован.")).toBeVisible();
});

test("аккаунт забанили при открытой сессии: экран блокировки без причины и выход", async ({
  page,
  scenario,
  expectAccessible,
}) => {
  await login(page);
  await scenario({ social: { sessionBanned: true } });
  await page.reload(); // сессия не принята (access истёк), refresh -> 403 auth.account_banned
  await expect(page).toHaveURL(/\/banned$/);
  await expect(page.getByRole("heading", { name: "Аккаунт заблокирован" })).toBeVisible();
  await expect(page.getByText(/причин/i)).toHaveCount(0);
  await expectAccessible(page);
  await page.getByTestId("banned-logout").click();
  await expect(page).toHaveURL("/");
  await expect(page.getByTestId("user-name")).toHaveCount(0);
});

test("присутствие: после входа уходит сигнал на social", async ({ page }) => {
  const beat = page.waitForRequest(
    (r) => r.method() === "POST" && r.url().endsWith("/api/social/v1/me/presence"),
  );
  await login(page);
  await beat;
});
