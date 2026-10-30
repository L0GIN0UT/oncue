/**
 * Ключи i18n без пропусков: каждый статический ключ `t("...")` из исходников есть в ru.json,
 * значения не пустые, а ключи из серверных данных (режимы, пресеты сидов) переведены.
 */
import { readdirSync, readFileSync, statSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import messages from "../../../messages/ru.json";
import { SOCIAL_ERROR_CODES, SOCIAL_EXTRA_KEYS } from "@/features/friends/errors";
import { CHAT_REJECT_REASONS, MATCH_ERROR_CODES } from "@/features/match/errors";

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
      ...["not_started", "in_progress", "finished"].map((s) => `daily.action.${s}`),
      ...["exact", "partial", "miss", "skip"].map((k) => `game.guess.${k}`),
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
      // Этап 3a: перечисления контрактов text_asset, presentation, туры сетки, варианты пары.
      ...["year", "genres", "country", "director", "cast_top3"].map((k) => `game.textField.${k}`),
      ...["not_started", "in_progress", "finished"].map((s) => `daily.statusPlain.${s}`),
      ...["score", "streak", "bracket"].flatMap((p) => [`solo.sub.${p}`, `records.best.${p}`]),
      ...["score", "streak"].map((u) => `results.record.best.${u}`),
      ...["final", "semifinal", "fraction"].map((k) => `game.bracket.${k}`),
      ...["higher", "lower"].map((k) => `game.options.${k}`),
      ...["random", "marathon", "pair"].map((g) => `solo.group.${g}.title`),
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
      // Этап 4a: онлайн — перечисления match и коды ошибок из описаний ручек OpenAPI.
      ...["duel", "party", "lobby"].map((k) => `online.kind.${k}`),
      ...["win", "loss", "draw"].map((k) => `online.outcome.${k}`),
      ...["completed", "forfeit", "left", "tech_win", "tech_loss"].map((k) => `online.finish.${k}`),
      ...["thinking", "answered", "missed", "guessed"].map((k) => `match.status.${k}`),
      ...["disconnected", "left"].map((k) => `match.presence.${k}`),
      ...[
        "rounds",
        "level_window_s",
        "players_max",
        "answer_format",
        "clue_type",
        "visibility",
      ].map((k) => `match.settings.${k}`),
      ...["public", "private"].map((k) => `match.settings.visibilityValue.${k}`),
      ...["generic", "network", "guest_forbidden", ...MATCH_ERROR_CODES].map(
        (k) => `match.error.${k}`,
      ),
      ...CHAT_REJECT_REASONS.map((k) => `match.error.chat_rejected_${k}`),
      // Этап 4b: рейтинг — ladder-ы и форматы очереди, подсказки форматов.
      ...["film", "music"].map((k) => `rating.ladder.${k}`),
      ...["classic", "lite"].flatMap((k) => [`rating.format.${k}`, `rating.formatHint.${k}`]),
      // Базовые эмоции и фразы онлайн-шаблонов (session.chat в сидах режимов).
      ...["wow", "gg", "fire", "laugh", "think"].map((k) => `match.emotes.${k}`),
      ...["good_luck", "well_played", "nice_one", "so_close"].map((k) => `match.phrases.${k}`),
      // Этап 5c: social — коды ошибок, статусы, виды санкций, объекты жалоб, приватность.
      ...[...SOCIAL_ERROR_CODES, ...SOCIAL_EXTRA_KEYS].map((k) => `social.error.${k}`),
      "auth.error.account_banned",
      ...["online", "in_match", "offline"].map((k) => `social.status.${k}`),
      ...["ranked", "chat", "online", "account"].flatMap((k) => [
        `social.sanctions.kind.${k}`,
        `social.sanctions.kindHint.${k}`,
      ]),
      ...["player", "collection", "comment", "avatar"].map((k) => `social.report.title.${k}`),
      ...["all", "none"].map((k) => `social.privacy.requestsValue.${k}`),
    ];
    expect(dynamic.filter((k) => typeof lookup(k) !== "string")).toEqual([]);
  });

  it("переводит эмоции и фразы session.chat онлайн-шаблонов из сидов", () => {
    const dir = path.resolve(root, "..", "seeds", "game", "modes");
    const keys = readdirSync(dir).flatMap((f) =>
      [...readFileSync(path.join(dir, f), "utf8").matchAll(/match\.(?:emotes|phrases)\.\w+/g)].map(
        (m) => m[0],
      ),
    );
    expect(keys.filter((k) => typeof lookup(k) !== "string")).toEqual([]);
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

  it("переводит звания ladder-ов (title_key) из примеров контракта и сидов rating", () => {
    const sources = [
      path.resolve(root, "..", "contracts", "rating", "examples"),
      path.resolve(root, "..", "seeds", "rating", "ladders"),
    ];
    const keys = sources.flatMap((dir) =>
      readdirSync(dir).flatMap((f) =>
        [
          ...readFileSync(path.join(dir, f), "utf8").matchAll(/title_key"?:\s*"?(rating\.[\w.]+)/g),
        ].map((m) => m[1]!),
      ),
    );
    expect(keys.length).toBeGreaterThan(5);
    expect(keys.filter((k) => typeof lookup(k) !== "string")).toEqual([]);
  });

  it("в разделе rating нет мёртвых ключей: каждый используется в исходниках", () => {
    // Звания, ladder-ы и форматы приходят с сервера — они проверяются отдельно (по сидам и контракту).
    const dynamicGroups = new Set(["tier", "ladder", "format", "formatHint"]);
    const source = files(path.join(root, "src"))
      .map((f) => readFileSync(f, "utf8"))
      .join("\n");
    const dead = leaves(messages.rating)
      .map(([key]) => key)
      .filter((key) => !dynamicGroups.has(key.split(".")[0]!))
      .filter((key) => !source.includes(`"rating.${key}"`));
    expect(dead).toEqual([]);
  });

  it("не содержит пустых значений", () => {
    expect(leaves(messages).filter(([, v]) => typeof v !== "string" || !v.trim())).toEqual([]);
  });
});
