import { expect, test } from "./fixtures";

test("регистрация → подтверждение e-mail → вход", async ({ page, expectAccessible }) => {
  await page.goto("/register");
  await expectAccessible(page);
  await page.getByLabel("Ник").fill("newbie");
  await page.getByLabel("E-mail").fill("newbie@example.com");
  await page.getByLabel("Пароль").fill("long-enough-pass");
  await page.getByRole("button", { name: "Зарегистрироваться" }).click();
  await expect(page.getByText("Отметьте все согласия.")).toBeVisible();
  for (const box of await page.getByRole("checkbox").all()) await box.check();
  await page.getByRole("button", { name: "Зарегистрироваться" }).click();
  await expect(page.getByTestId("register-done")).toBeVisible();

  await page.goto("/verify-email?token=valid-token");
  await expect(page.getByTestId("verify-state")).toHaveText(
    "E-mail подтверждён. Теперь можно войти.",
  );

  await page.goto("/login");
  await page.getByLabel("E-mail или ник").fill("newbie");
  await page.getByLabel("Пароль").fill("long-enough-pass");
  await page.getByRole("button", { name: "Войти" }).click();
  await expect(page).toHaveURL("/");
  await expect(page.getByTestId("user-name")).toHaveText("newbie");
});

test("вход с неверным паролем показывает ошибку; next ведёт только внутрь сайта", async ({
  page,
}) => {
  await page.goto("/login?next=https://evil.example/");
  await page.getByLabel("E-mail или ник").fill("player");
  await page.getByLabel("Пароль").fill("wrong-password");
  await page.getByRole("button", { name: "Войти" }).click();
  await expect(page.getByText("Неверный логин или пароль.")).toBeVisible();
  await page.getByLabel("Пароль").fill("correct-horse");
  await page.getByRole("button", { name: "Войти" }).click();
  await expect(page).toHaveURL("/");
  await expect(page.getByTestId("user-name")).toHaveText("player");
});

test("ссылка из письма /auth/verify-email перенаправляется на /verify-email", async ({ page }) => {
  await page.goto("/auth/verify-email?token=bad");
  await expect(page).toHaveURL(/\/verify-email\?token=bad$/);
  await expect(page.getByTestId("verify-state")).toContainText("недействительна");
});
