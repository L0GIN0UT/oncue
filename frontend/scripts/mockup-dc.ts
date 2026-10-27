/**
 * Снимки интерактивных бордов (`*.dc.html` с `{{…}}`, `sc-for`, `sc-if`): среды макетов под рукой
 * нет, поэтому здесь минимальный исполнитель шаблона — достаточно, чтобы увидеть состояния Game,
 * Reveal и Event так, как их показывает канвас. Запуск: `npx tsx scripts/mockup-dc.ts [Имя…]` →
 * `docs/handoff/shots/<имя>-mockup-1280.png`; состояния — в STATES ниже. Выполняет только борды из
 * репозитория (`design/mockups`), чужой разметке не предназначен.
 */
import { mkdirSync } from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { chromium, type Page } from "@playwright/test";

const SRC = path.resolve(import.meta.dirname, "../design/mockups");
const OUT = path.resolve(import.meta.dirname, "../../docs/handoff/shots");

/** Исполнитель шаблона: выполняется внутри страницы борда. */
const RUNTIME = `(props) => {
  const host = document.querySelector("x-dc");
  const scriptEl = document.querySelector("script[data-dc-script]");
  const template = document.createElement("template");
  template.innerHTML = host.innerHTML;
  class DCLogic {
    constructor(p) { this.props = p || {}; }
    setState(patch) { Object.assign(this.state, typeof patch === "function" ? patch(this.state) : patch); window.__dcRender(); }
  }
  const declared = JSON.parse(scriptEl.dataset.props || "{}");
  const defaults = {};
  for (const [k, v] of Object.entries(declared)) if (v && typeof v === "object" && "default" in v) defaults[k] = v.default;
  const Component = new Function("DCLogic", scriptEl.textContent + "; return Component;")(DCLogic);
  const logic = new Component({ ...defaults, ...props });
  const run = (expr, scope) => new Function(...Object.keys(scope), "return (" + expr + ")")(...Object.values(scope));
  const text = (s, scope) => s.replace(/\\{\\{\\s*([^}]+?)\\s*\\}\\}/g, (_, e) => { const v = run(e, scope); return v == null ? "" : String(v); });
  const only = (s) => { const m = s.match(/^\\s*\\{\\{\\s*([^}]+?)\\s*\\}\\}\\s*$/); return m ? m[1] : null; };
  function walk(parent, scope) {
    for (const node of Array.from(parent.childNodes)) {
      if (node.nodeType === 3) { node.textContent = text(node.textContent, scope); continue; }
      if (node.nodeType !== 1) continue;
      const tag = node.tagName.toLowerCase();
      if (tag === "sc-for") {
        const list = run(only(node.getAttribute("list")), scope) || [];
        const name = node.getAttribute("as");
        const out = document.createDocumentFragment();
        for (const item of list) {
          const copy = node.cloneNode(true);
          walk(copy, { ...scope, [name]: item });
          while (copy.firstChild) out.appendChild(copy.firstChild);
        }
        node.replaceWith(out);
        continue;
      }
      if (tag === "sc-if") {
        if (run(only(node.getAttribute("value")), scope)) { walk(node, scope); node.replaceWith(...Array.from(node.childNodes)); }
        else node.remove();
        continue;
      }
      for (const attr of Array.from(node.attributes)) {
        if (!attr.value.includes("{{")) continue;
        const single = only(attr.value);
        if (/^on/i.test(attr.name) && single) {
          const fn = run(single, scope);
          node.removeAttribute(attr.name);
          if (typeof fn === "function") node.addEventListener(attr.name.slice(2).toLowerCase(), fn);
        } else node.setAttribute(attr.name, text(attr.value, scope));
      }
      walk(node, scope);
    }
  }
  window.__dcRender = () => {
    const root = template.content.cloneNode(true);
    walk(root, logic.renderVals());
    host.replaceChildren(root);
  };
  window.__dc = logic;
  window.__dcRender();
}`;

type State = {
  name: string;
  board: string;
  props?: Record<string, unknown>;
  /** Действия над отрисованным бордом до снимка. */
  act?: (page: Page) => Promise<void>;
};

const STATES: State[] = [
  { name: "game", board: "Game" },
  {
    name: "game-answer",
    board: "Game",
    act: async (page) => {
      await page.getByRole("button", { name: "Пропустить" }).click();
      await page.locator("#guess").fill("Creep");
      await page.getByRole("option", { name: /Creep/ }).first().click();
    },
  },
  {
    name: "game-reveal",
    board: "Game",
    act: async (page) => {
      await page.getByRole("button", { name: "Пропустить" }).click();
      await page.locator("#guess").fill("Creep");
      await page.getByRole("option", { name: /Creep/ }).first().click();
      await page.getByRole("button", { name: "Ответить" }).click();
    },
  },
  { name: "reveal", board: "Reveal" },
  { name: "reveal-film", board: "Reveal", props: { domain: "film", outcome: "partial" } },
  { name: "reveal-skip", board: "Reveal", props: { outcome: "skip", last: true } },
  { name: "event", board: "Event" },
];

async function main() {
  const only = process.argv.slice(2).map((s) => s.toLowerCase());
  mkdirSync(OUT, { recursive: true });
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
    for (const state of STATES) {
      if (only.length && !only.includes(state.name)) continue;
      await page.goto(pathToFileURL(path.join(SRC, `${state.board}.dc.html`)).href);
      await page.evaluate(`(${RUNTIME})(${JSON.stringify(state.props ?? {})})`);
      await state.act?.(page);
      await page.waitForTimeout(500);
      const file = path.join(OUT, `${state.name}-mockup-1280.png`);
      await page.screenshot({ path: file });
      console.log("макет:", path.relative(process.cwd(), file));
    }
  } finally {
    await browser.close();
  }
}

void main();
