/**
 * Ключи i18n без пропусков: каждый статический ключ `t("...")` из исходников есть в ru.json,
 * значения не пустые, а ключи из серверных данных (режимы, пресеты сидов) переведены.
 */
import { readdirSync, readFileSync, statSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import messages from "../../../messages/ru.json";

const root = path.resolve(import.meta.dirname, "../../..");

function files(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const full = path.join(dir, name);
    if (statSync(full).isDirectory()) return name === "schema" ? [] : files(full);
    return /\.(ts|tsx)$/.test(name) && !/\.test\./.test(name) ? [full] : [];
  });
}

function lookup(key: string): unknown {
  return key
    .split(".")
    .reduce<unknown>(
      (node, part) =>
        node && typeof node === "object" ? (node as Record<string, unknown>)[part] : undefined,
      messages,
    );
}

function leaves(node: unknown, prefix = ""): [string, unknown][] {
  if (node && typeof node === "object")
    return Object.entries(node).flatMap(([k, v]) => leaves(v, prefix ? `${prefix}.${k}` : k));
  return [[prefix, node]];
}

describe("messages/ru.json", () => {
  it("содержит все статические ключи из исходников", () => {
    const missing: string[] = [];
    for (const file of files(path.join(root, "src"))) {
      const source = readFileSync(file, "utf8");
      for (const m of source.matchAll(/\bt\(\s*"([\w.]+)"/g)) {
        if (typeof lookup(m[1]!) !== "string")
          missing.push(`${path.relative(root, file)}: ${m[1]}`);
      }
    }
    expect(missing).toEqual([]);
  });

  it("покрывает ключи с подстановкой из перечислений контракта", () => {
    const dynamic = [
      ...["music", "film"].flatMap((d) => [
        `domain.${d}`,
        `daily.pitch.${d}`,
        `game.answerLabel.${d}`,
        `game.answerPlaceholder.${d}`,
      ]),
      ...["not_started", "in_progress", "finished"].flatMap((s) => [
        `daily.status.${s}`,
        `daily.action.${s}`,
      ]),
      ...["exact", "partial", "miss", "skip"].map((k) => `game.guess.${k}`),
      ...["exact", "partial", "miss", "void", "active", "pending"].map((k) => `game.outcome.${k}`),
      ...["exact", "partial", "miss", "void"].map((k) => `game.reveal.${k}`),
      ...["rate_limited", "round_void", "network", "desync", "failed"].map(
        (k) => `game.notice.${k}`,
      ),
      ...["exact", "partial", "miss", "gave_up", "void"].map((k) => `results.outcome.${k}`),
      ...[
        "guest_not_allowed",
        "daily_not_ready",
        "mode_not_found",
        "rate_limited",
        "failed",
      ].flatMap((k) => [`start.error.${k}.title`, `start.error.${k}.text`]),
      ...["pending", "ok", "failed"].map((k) => `auth.verify.${k}`),
      // Этап 3a: перечисления контрактов text_asset, presentation, туры сетки, варианты пары.
      ...["year", "genres", "country", "director", "cast_top3"].map((k) => `game.textField.${k}`),
      ...["not_started", "in_progress", "finished"].map((s) => `daily.statusPlain.${s}`),
      ...["score", "streak", "bracket"].flatMap((p) => [
        `solo.presentation.${p}`,
        `records.best.${p}`,
      ]),
      ...["score", "streak"].map((u) => `results.record.best.${u}`),
      ...["final", "semifinal", "fraction"].map((k) => `game.bracket.${k}`),
      ...["higher", "lower"].map((k) => `game.options.${k}`),
      ...["random", "marathon", "pair"].flatMap((g) => [
        `solo.group.${g}.title`,
        `solo.group.${g}.text`,
      ]),
      ...["streak", "newRecord", "champion"].map((k) => `results.shareText.${k}`),
      // Этап 3b: сборники и медиатека.
      ...["collection_not_found", "rounds_not_allowed", "pool_too_small"].flatMap((k) => [
        `start.error.${k}.title`,
        `start.error.${k}.text`,
      ]),
      ...["easy", "medium", "hard"].map((d) => `collections.difficulty.${d}`),
      ...["popular", "new", "likes", "difficulty", "featured"].map((s) => `collections.sort.${s}`),
      ...["featured", "popular", "new"].map((b) => `collections.blocks.${b}`),
      ...["draft", "published", "hidden"].map((s) => `collections.status.${s}`),
      ...["published", "drafts"].flatMap((k) => [
        `collections.mine.${k}`,
        `collections.mine.empty.${k}`,
      ]),
      ...["empty", "not_emoji", "too_short", "too_long"].map(
        (k) => `collections.editor.emojiCheck.${k}`,
      ),
      ...["music", "film"].map((d) => `collections.editor.searchPlaceholder.${d}`),
      // Коды публикации (details.errors[].code) и причины gv_text.ugc (reason).
      ...[
        "items_too_few",
        "items_too_many",
        "item_duplicate",
        "item_domain_mismatch",
        "item_unavailable",
        "mode_invalid",
        "emoji_invalid",
        "emoji_too_short",
        "tag_unknown",
        "tags_too_many",
        "cover_unknown",
        "title_invalid",
        "description_invalid",
        "draft_conflict",
        "drafts_limit",
        "publish_limit",
        "own_collection",
      ].map((k) => `errors.collections.${k}`),
      ...[
        "empty",
        "too_long",
        "denied_word",
        "link_forbidden",
        "mention_forbidden",
        "email_forbidden",
      ].map((k) => `errors.ugc.${k}`),
    ];
    expect(dynamic.filter((k) => typeof lookup(k) !== "string")).toEqual([]);
  });

  it("переводит title_key тегов collections и системных полок library из их конфигов", () => {
    const configs = [
      "services/collections/src/collections_service/config/meta.yaml",
      "services/library/src/library_service/config/shelves.yaml",
    ];
    const keys = configs.flatMap((f) =>
      [...readFileSync(path.resolve(root, "..", f), "utf8").matchAll(/title_key:\s*([\w.]+)/g)].map(
        (m) => m[1]!,
      ),
    );
    expect(keys.length).toBeGreaterThan(3);
    expect(keys.filter((k) => typeof lookup(k) !== "string")).toEqual([]);
  });

  it("переводит title_i18n режимов и пресетов из сидов seeds/game/modes", () => {
    const dir = path.resolve(root, "..", "seeds", "game", "modes");
    const keys = readdirSync(dir).flatMap((f) =>
      [...readFileSync(path.join(dir, f), "utf8").matchAll(/title_i18n:\s*([\w.]+)/g)].map(
        (m) => m[1]!,
      ),
    );
    expect(keys.length).toBeGreaterThan(0);
    expect(keys.filter((k) => typeof lookup(k) !== "string")).toEqual([]);
  });

  it("переводит description_i18n режимов из сидов (карточки витрины и билеты)", () => {
    const dir = path.resolve(root, "..", "seeds", "game", "modes");
    const keys = readdirSync(dir).flatMap((f) =>
      [...readFileSync(path.join(dir, f), "utf8").matchAll(/description_i18n:\s*([\w.]+)/g)].map(
        (m) => m[1]!,
      ),
    );
    expect(keys.length).toBeGreaterThan(0);
    expect(keys.filter((k) => typeof lookup(k) !== "string")).toEqual([]);
  });

  it("не содержит пустых значений", () => {
    expect(leaves(messages).filter(([, v]) => typeof v !== "string" || !v.trim())).toEqual([]);
  });
});
