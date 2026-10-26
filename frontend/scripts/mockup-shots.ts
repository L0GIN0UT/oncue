/**
 * Снимки макетов v3 для сравнения с результатом: `design/mockups/<Имя>.dc.html` → 1280×900 в
 * `docs/handoff/shots/<имя>-mockup-1280.png`. Статичные борды отрисовываются и без среды макетов
 * (разметка обычный HTML), интерактивные показывают первое состояние. Запуск:
 * `npx tsx scripts/mockup-shots.ts Solo Online …`.
 */
import { mkdirSync } from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { chromium } from "@playwright/test";

const SRC = path.resolve(import.meta.dirname, "../design/mockups");
const OUT = path.resolve(import.meta.dirname, "../../docs/handoff/shots");

async function main() {
  const names = process.argv.slice(2);
  mkdirSync(OUT, { recursive: true });
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
    for (const name of names) {
      await page.goto(pathToFileURL(path.join(SRC, `${name}.dc.html`)).href);
      await page.waitForTimeout(800);
      const file = path.join(OUT, `${name.toLowerCase()}-mockup-1280.png`);
      await page.screenshot({ path: file });
      console.log("макет:", file);
    }
  } finally {
    await browser.close();
  }
}

void main();
