/**
 * Шапка на узком экране: колокольчик, баланс, медиатека, лидеры и профиль помещаются в ширину
 * экрана, не налезают друг на друга и не дают горизонтальной прокрутки.
 */
import { expect, login, test } from "./fixtures";

const TEST_IDS = ["nav-verses", "nav-library", "nav-leaders", "nav-profile"] as const;

for (const width of [320, 360, 390]) {
  test(`шапка пользователя на ${width} px не переполняется`, async ({ page }) => {
    await page.setViewportSize({ width, height: 740 });
    await login(page);
    await page.goto("/");

    const bell = page.getByRole("button", { name: "Уведомления" }).first();
    const boxes = [
      await bell.boundingBox(),
      ...(await Promise.all(TEST_IDS.map((id) => page.getByTestId(id).boundingBox()))),
    ];
    for (const box of boxes) {
      expect(box).not.toBeNull();
      expect(box!.x).toBeGreaterThanOrEqual(0);
      expect(box!.x + box!.width).toBeLessThanOrEqual(width + 0.5);
    }
    // Друг на друга не налезают.
    for (let i = 0; i < boxes.length; i++) {
      for (let j = i + 1; j < boxes.length; j++) {
        const a = boxes[i]!;
        const b = boxes[j]!;
        const apart =
          a.x + a.width <= b.x + 0.5 ||
          b.x + b.width <= a.x + 0.5 ||
          a.y + a.height <= b.y + 0.5 ||
          b.y + b.height <= a.y + 0.5;
        expect(apart, `элементы шапки ${i} и ${j} пересекаются`).toBe(true);
      }
    }
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(overflow).toBeLessThanOrEqual(0);
  });
}
