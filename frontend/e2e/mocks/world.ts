/**
 * Состояние мок-бэкенда по контрактам (contracts/openapi/{game,bff_web,catalog,identity}.json;
 * сборники и медиатека этапа 3b — e2e/mocks/ugc.ts).
 *
 * Общий для unit-тестов (MSW в jsdom) и Playwright (HTTP-сервер e2e/mocks/server.ts).
 * Логика игры упрощена, но соблюдает инварианты контракта: до ревила в снимке нет
 * `media_item_id` и названия, ассет выдаётся только для уровня ≤ текущего, у хода есть
 * `expected_version` и идемпотентность по ключу.
 */
import type {
  DailyCard,
  DailyStatus,
  ModeOut,
  ResultsPage,
  RoundOut,
  SessionResult,
  SessionSnapshot,
  Suggestion,
} from "@/shared/api/types";
import { dayIn, nextMidnight } from "@/features/daily/countdown-time";
import { ProgressionWorld } from "./progression";
import { EconomyWorld } from "./economy";
import { NotificationsWorld } from "./notifications";
import { COLLECTION_MODES, UgcWorld } from "./ugc";

export type Domain = "music" | "film";

export type MockItem = {
  id: string;
  domain: Domain;
  title: string;
  year: number;
  /** Исполнитель (музыка) или режиссёр (кино). */
  person: { name: string; role: "artist" | "director" };
  genres: string[];
};

const uuid = (n: number) => `00000000-0000-4000-8000-${String(n).padStart(12, "0")}`;

export const ITEMS: MockItem[] = [
  {
    id: uuid(101),
    domain: "music",
    title: "Группа крови",
    year: 1988,
    person: { name: "Кино", role: "artist" },
    genres: ["rock"],
  },
  {
    id: uuid(102),
    domain: "music",
    title: "Звезда по имени Солнце",
    year: 1989,
    person: { name: "Кино", role: "artist" },
    genres: ["rock"],
  },
  {
    id: uuid(103),
    domain: "music",
    title: "Районы-кварталы",
    year: 1999,
    person: { name: "Звери", role: "artist" },
    genres: ["pop"],
  },
  {
    id: uuid(104),
    domain: "music",
    title: "Прогулки",
    year: 2003,
    person: { name: "Звери", role: "artist" },
    genres: ["pop"],
  },
  {
    id: uuid(105),
    domain: "music",
    title: "Последний герой",
    year: 1989,
    person: { name: "Кино", role: "artist" },
    genres: ["rock"],
  },
  {
    id: uuid(106),
    domain: "music",
    title: "Зима",
    year: 2001,
    person: { name: "Сплин", role: "artist" },
    genres: ["rock"],
  },
  {
    id: uuid(107),
    domain: "music",
    title: "Выхода нет",
    year: 2001,
    person: { name: "Сплин", role: "artist" },
    genres: ["rock"],
  },
  {
    id: uuid(108),
    domain: "music",
    title: "Танцы",
    year: 2005,
    person: { name: "Звери", role: "artist" },
    genres: ["pop"],
  },
  {
    id: uuid(201),
    domain: "film",
    title: "Брат",
    year: 1997,
    person: { name: "Алексей Балабанов", role: "director" },
    genres: ["crime"],
  },
  {
    id: uuid(202),
    domain: "film",
    title: "Брат 2",
    year: 2000,
    person: { name: "Алексей Балабанов", role: "director" },
    genres: ["crime"],
  },
  {
    id: uuid(203),
    domain: "film",
    title: "Ирония судьбы",
    year: 1975,
    person: { name: "Эльдар Рязанов", role: "director" },
    genres: ["comedy"],
  },
  {
    id: uuid(204),
    domain: "film",
    title: "Солярис",
    year: 1972,
    person: { name: "Андрей Тарковский", role: "director" },
    genres: ["drama"],
  },
  {
    id: uuid(205),
    domain: "film",
    title: "Сталкер",
    year: 1979,
    person: { name: "Андрей Тарковский", role: "director" },
    genres: ["drama"],
  },
  {
    id: uuid(206),
    domain: "film",
    title: "Ночной дозор",
    year: 2004,
    person: { name: "Тимур Бекмамбетов", role: "director" },
    genres: ["fantasy"],
  },
  {
    id: uuid(207),
    domain: "film",
    title: "Девятая рота",
    year: 2005,
    person: { name: "Фёдор Бондарчук", role: "director" },
    genres: ["war"],
  },
  {
    id: uuid(208),
    domain: "film",
    title: "Служебный роман",
    year: 1977,
    person: { name: "Эльдар Рязанов", role: "director" },
    genres: ["comedy"],
  },
  // Ещё фильмы Тарковского: марафон режиссёра (≥ 5 фильмов персоны).
  {
    id: uuid(209),
    domain: "film",
    title: "Зеркало",
    year: 1974,
    person: { name: "Андрей Тарковский", role: "director" },
    genres: ["drama"],
  },
  {
    id: uuid(210),
    domain: "film",
    title: "Андрей Рублёв",
    year: 1966,
    person: { name: "Андрей Тарковский", role: "director" },
    genres: ["drama"],
  },
  {
    id: uuid(211),
    domain: "film",
    title: "Иваново детство",
    year: 1962,
    person: { name: "Андрей Тарковский", role: "director" },
    genres: ["war"],
  },
];

export const itemById = (id: string) => ITEMS.find((i) => i.id === id);

/** Метрика сравнения «больше/меньше» (рейтинг фильма, популярность трека): разная у всех. */
export function metricOf(item: MockItem): number {
  const n = Number(item.id.slice(-3));
  return Math.round((5 + ((n * 37) % 41) / 10) * 10) / 10;
}

/** Идентификатор персоны (как `subject_id` проекции game): по имени, детерминированно. */
export function personId(name: string): string {
  let h = 0;
  for (const c of name) h = (h * 31 + c.charCodeAt(0)) % 1_000_000;
  return `00000000-0000-4000-9000-${String(h).padStart(12, "0")}`;
}

const AUDIO_LEVELS = [0.5, 1, 2, 4, 8, 15].map((seconds, i) => ({
  index: i + 1,
  hint: { seconds },
}));
const FRAME_LEVELS = [48, 32, 20, 12, 6, 0].map((blur_px, i) => ({
  index: i + 1,
  hint: { blur_px, crop: [0.35, 0.5, 0.7, 1, 1, 1][i] ?? 1 },
}));
const SEQUENCE_LEVELS = [1, 2, 3, 4, 5, 6].map((frame, i) => ({ index: i + 1, hint: { frame } }));
/** Поля описания накапливаются от уровня к уровню (как `description.params`). */
const DESCRIPTION_FIELDS = [
  ["tagline"],
  ["plot_masked"],
  ["year", "genres"],
  ["country"],
  ["director"],
  ["cast_top3"],
];
const DESCRIPTION_LEVELS = DESCRIPTION_FIELDS.map((_, i) => ({
  index: i + 1,
  hint: { fields: DESCRIPTION_FIELDS.slice(0, i + 1).flat() },
}));
const EMOJI_COUNTS = [1, 2, 3, 4, 6, null];
const EMOJI_LEVELS = EMOJI_COUNTS.map((count, i) => ({ index: i + 1, hint: { count } }));
const STILL_LEVELS = [{ index: 1, hint: { blur_px: 0, crop: 1 } }];
const POINTS = [6, 5, 4, 3, 2, 1];
const PARTIAL_RATIO = 0.5;
/** Размер сетки мок-мира (в game — из конфига режима, засев — 32). */
export const BRACKET_SIZE = 8;

type ModeExtra = Pick<ModeOut, "answer_format" | "presentation" | "start" | "presets">;
const classic: ModeExtra = {
  answer_format: "classic",
  presentation: "score",
  start: null,
  presets: null,
};

const mode = (
  key: string,
  source_type: string,
  clue_type: string,
  levels: ModeOut["levels"],
  rounds: number,
  extra: Partial<ModeExtra> = {},
): ModeOut => ({
  key,
  version: 1,
  domain: key.startsWith("music.") ? "music" : "film",
  title_key: `modes.${key}.title`,
  source_type,
  clue_type,
  rounds,
  levels,
  allow_guest: true,
  ...classic,
  ...extra,
});

export const MODES: ModeOut[] = [
  mode("music.daily.classic", "daily", "audio_intro", AUDIO_LEVELS, 5),
  mode("film.daily.frame", "daily", "frame_progressive", FRAME_LEVELS, 5),
  mode("music.random.classic", "random", "audio_intro", AUDIO_LEVELS, 10, {
    presets: [
      { id: "y2000s", title_key: "modes.presets.y2000s" },
      { id: "y2010s", title_key: "modes.presets.y2010s" },
      { id: "rock", title_key: "modes.presets.rock" },
    ],
  }),
  mode("film.random.frame", "random", "frame_progressive", FRAME_LEVELS, 10, {
    presets: [
      { id: "y1990s", title_key: "modes.presets.y1990s" },
      { id: "y2000s", title_key: "modes.presets.y2000s" },
      { id: "comedy", title_key: "modes.presets.comedy" },
    ],
  }),
  // ------------------------------------------------------------- этап 3a
  mode("music.random.reverse", "random", "audio_reverse", AUDIO_LEVELS, 5),
  mode("film.random.description", "random", "description", DESCRIPTION_LEVELS, 5),
  mode("film.random.emoji", "random", "emoji", EMOJI_LEVELS, 5),
  mode("film.random.soundtrack", "random", "soundtrack", AUDIO_LEVELS, 5),
  mode("film.random.blackout", "random", "audio_blackout", AUDIO_LEVELS, 5),
  mode("film.random.sequence", "random", "frame_sequence", SEQUENCE_LEVELS, 5),
  mode("film.marathon.director", "marathon", "frame_progressive", FRAME_LEVELS, 200, {
    start: { subject: { roles: ["director"], max: 3 } },
  }),
  mode("music.marathon.artist", "marathon", "audio_intro", AUDIO_LEVELS, 200, {
    start: { subject: { roles: ["artist"], max: 3 } },
  }),
  mode("film.pair.rating", "pair", "frame_progressive", STILL_LEVELS, 200, {
    answer_format: "pick",
    presentation: "streak",
  }),
  mode("music.pair.popularity", "pair", "audio_intro", [AUDIO_LEVELS[5]!], 200, {
    answer_format: "pick",
    presentation: "streak",
  }),
  mode("film.bracket.weekly", "bracket", "frame_progressive", STILL_LEVELS, BRACKET_SIZE - 1, {
    answer_format: "pick",
    presentation: "bracket",
  }),
];

/** Правила мок-режимов по `source_type` (в game — плагины ядра; мок упрощает). */
const SCHEDULED = new Set(["daily", "bracket"]);
const WEEKLY = new Set(["bracket"]);
/** Жизни режима (`session.end: lives`). */
const LIVES: Record<string, number> = { marathon: 3, pair: 1 };

/** Вид выдачи по типу загадки (как `ClueView.kind` плагинов ядра). */
const CLUE_KIND: Record<string, "audio" | "image" | "text"> = {
  audio_intro: "audio",
  audio_reverse: "audio",
  soundtrack: "audio",
  audio_blackout: "audio",
  frame_progressive: "image",
  frame_sequence: "image",
  poster_masked: "image",
  description: "text",
  emoji: "text",
};
/** Ревил ассетом другого вида (`clue.reveal`, ADR 0026): звук в темноте → кадр. */
const REVEAL_KIND: Record<string, "audio" | "image"> = { audio_blackout: "image" };

const clueKindOf = (m: ModeOut) => CLUE_KIND[m.clue_type] ?? "image";
const revealKindOf = (m: ModeOut): "audio" | "image" => {
  const kind = REVEAL_KIND[m.clue_type] ?? clueKindOf(m);
  return kind === "text" ? "image" : kind;
};

/** Наборы дня: фиксированные пять единиц на режим. */
export const DAILY_SETS: Record<string, string[]> = {
  "music.daily.classic": [uuid(101), uuid(103), uuid(106), uuid(104), uuid(107)],
  "film.daily.frame": [uuid(201), uuid(203), uuid(205), uuid(206), uuid(208)],
};

const PRESET_FILTERS: Record<string, (i: MockItem) => boolean> = {
  y1990s: (i) => i.year >= 1990 && i.year <= 1999,
  y2000s: (i) => i.year >= 2000 && i.year <= 2009,
  y2010s: (i) => i.year >= 2010 && i.year <= 2019,
  rock: (i) => i.genres.includes("rock"),
  comedy: (i) => i.genres.includes("comedy"),
};

/** Доля угадавших по раундам дейлика (для экрана итога). */
const DAILY_EXACT_PCT = [62.5, 41, 18.2, 33, 7.5];

/** Эмодзи фильма для загадки `emoji` (по жанру; уровень открывает первые `count`). */
const EMOJI: Record<string, string[]> = {
  crime: ["🔫", "🚗", "💰", "🧥", "🎸", "🏙️"],
  comedy: ["❄️", "🛁", "✈️", "🥂", "🎄", "😂"],
  drama: ["🌊", "🚀", "🧠", "🌌", "👁️", "🕯️"],
  fantasy: ["🌃", "🧛", "🔦", "⚔️", "🌀", "🌆"],
  war: ["🪖", "⛰️", "💥", "📻", "🎖️", "🚁"],
  rock: ["🎸", "🥁", "🎤", "🔥", "🌙", "⭐"],
  pop: ["💃", "🎶", "🌆", "💖", "🕺", "✨"],
};

type Option = { option_id: string; label_key: string | null; itemId: string | null };

type Round = {
  index: number;
  itemId: string;
  status: RoundOut["status"];
  level: number;
  guesses: RoundOut["guesses"];
  partialLevel: number | null;
  outcome: "exact" | "partial" | "miss" | "gave_up" | "void" | null;
  points: number | null;
  /** Открыто показанные единицы (опорная единица пары, варианты сетки). */
  shown: string[];
  options: Option[];
  /** Размер тура сетки. */
  stage: number | null;
  /** Выбранный вариант (сетка: победитель пары). */
  picked: string | null;
};

export type MockSession = {
  id: string;
  subject: string;
  mode: ModeOut;
  day: string | null;
  version: number;
  status: SessionSnapshot["status"];
  current: number;
  rounds: Round[];
  lives: number | null;
  /** Сессия по сборнику (этап 3b): `collection` итога. */
  collection: { id: string; version: number } | null;
};

export type MockUser = {
  id: string;
  nickname: string;
  email: string;
  password: string;
  verified: boolean;
};

export type Scenario = {
  /** Следующий ход ответит 409 `game.desync` с актуальным снимком (версия сервера ушла вперёд). */
  desyncOnce?: boolean;
  /** Следующая выдача ассета ответит 410 `game.asset_unavailable`, раунд станет `void`. */
  assetGoneOnce?: boolean;
  /** Дейлики не готовы: старт — 503 `game.daily_not_ready`. */
  dailyNotReady?: boolean;
  /** bff: блок дейликов недоступен. */
  dailiesUnavailable?: boolean;
  /**
   * Ссылки ревила в снимках «протухли»: файл по ним отвечает 403 (как истёкшая подпись),
   * свежую ссылку даёт только `POST .../final-clue`.
   */
  staleRevealLinks?: boolean;
  /** Этап 5a: блоки профиля (`progress`, `wallet`, `quests`, `badges`, `wheel`) и `home`, которые гаснут. */
  progressDown?: string[];
  /** Этап 5a: спин колеса отвечает 409 `progression.wheel_disabled`. */
  spinConflict?: boolean;
  /** Этап 5a: первый спин обрывается сетью (повтор проходит). */
  spinNetworkOnce?: boolean;
  /** Этап 5a: игрок уже крутил в другой вкладке — первый спин вернёт `repeated: true`. */
  spunElsewhere?: boolean;
  /** Этап 5b: ключ темы в ответе shell (по умолчанию `default`). */
  themeKey?: string;
  /** Этап 5b: shell отвечает 503 (тема откатывается на default). */
  shellDown?: boolean;
  /** Этап 5b: витрина магазина недоступна. */
  shopDown?: boolean;
  /** Этап 5b: блоки appearance/inventory/avatar профиля недоступны. */
  appearanceDown?: boolean;
  /** Этап 5b: стартовые Версы (применяются до первой покупки). */
  balance?: number;
  /** Этап 5b: погашение промокода — 429 с Retry-After 3. */
  promoRateLimited?: boolean;
  /** Этап 5b: ошибка загрузки аватарки. */
  avatarFail?: "too_large" | "invalid" | "rate";
  /** Этап 5b: предустановленная заявка на аватарку (право считается купленным). */
  avatarRequest?: "pending" | "approved" | "rejected";
  /** Этап 5b: notifications отвечает 503. */
  notificationsDown?: boolean;
};

type Asset =
  | { kind: "audio" | "image"; seconds: number; blur: number; seed: number }
  | { kind: "text"; body: unknown };

const blank = (index: number, itemId: string): Round => ({
  index,
  itemId,
  status: index === 0 ? "active" : "pending",
  level: 1,
  guesses: [],
  partialLevel: null,
  outcome: null,
  points: null,
  shown: [],
  options: [],
  stage: null,
  picked: null,
});

const LOST = new Set(["miss", "gave_up"]);

/** Пользователи мок-мира: игрок, автор сборников (этап 3b) и соперники онлайна (этап 4a). */
const BASE_USERS: MockUser[] = [
  {
    id: uuid(901),
    nickname: "player",
    email: "player@example.com",
    password: "correct-horse",
    verified: true,
  },
  {
    id: uuid(902),
    nickname: "author",
    email: "author@example.com",
    password: "correct-horse",
    verified: true,
  },
  // Соперники онлайна (этап 4a): несколько контекстов браузера в одной комнате.
  ...["rival", "third", "fourth"].map((nickname, i) => ({
    id: uuid(903 + i),
    nickname,
    email: `${nickname}@example.com`,
    password: "correct-horse",
    verified: true,
  })),
];

export class MockWorld {
  sessions = new Map<string, MockSession>();
  idempotency = new Map<string, { status: number; body: unknown }>();
  users: MockUser[] = BASE_USERS.map((u) => ({ ...u }));
  /** Сборники и медиатека (этап 3b). */
  ugc = new UgcWorld();
  /** Прогрессия (этап 5a): сегодняшние спины колеса. */
  progression = new ProgressionWorld();
  /** Магазин, оформление, аватарка (этап 5b). */
  economy = new EconomyWorld();
  /** Уведомления (этап 5b). */
  notifications = new NotificationsWorld();
  scenario: Scenario = {};
  /** Субъекты, чья refresh-сессия отозвана (refresh отвечает 401). */
  revokedRefresh = new Set<string>();
  /** Сколько раз сессия обновлялась через refresh. */
  refreshCalls = 0;
  /** Сколько раз выдавалась свежая ссылка ревила (`final-clue`). */
  finalClueCalls = 0;
  /** Журнал выданных ссылок на ассеты: путь → параметры (для отдачи «файла»). */
  assets = new Map<string, Asset>();
  /** Лучшие результаты пользователей по режимам (рекорды, D11). */
  records = new Map<string, number>();
  private seq = 0;
  now: () => Date = () => new Date();

  reset(): void {
    this.sessions.clear();
    this.idempotency.clear();
    this.assets.clear();
    this.records.clear();
    this.scenario = {};
    this.revokedRefresh.clear();
    this.refreshCalls = 0;
    this.finalClueCalls = 0;
    this.users = BASE_USERS.map((u) => ({ ...u }));
    this.ugc.reset();
    this.progression.reset();
    this.economy.reset();
    this.notifications.reset();
    this.seq = 0;
  }

  nextId(prefix: number): string {
    this.seq += 1;
    return `0192a000-0000-7000-8${String(prefix).padStart(3, "0")}-${String(this.seq).padStart(12, "0")}`;
  }

  today(): string {
    return dayIn(this.now());
  }

  nextResetAt(): string {
    return nextMidnight(this.now()).toISOString();
  }

  /** Конец недели периода сетки (мок: через 7 дней после ближайшей полуночи). */
  nextWeekResetAt(): string {
    return new Date(nextMidnight(this.now()).getTime() + 6 * 86_400_000).toISOString();
  }

  /** Режим по ключу, включая шаблоны сборников (`GET /v1/modes?kind=collection`). */
  mode(key: string): ModeOut | undefined {
    return MODES.find((m) => m.key === key) ?? COLLECTION_MODES.find((m) => m.key === key);
  }

  isScheduled(m: ModeOut): boolean {
    return SCHEDULED.has(m.source_type);
  }

  dailyFor(subject: string, modeKey: string): MockSession | undefined {
    const day = this.today();
    return [...this.sessions.values()].find(
      (s) => s.subject === subject && s.mode.key === modeKey && s.day === day,
    );
  }

  // ------------------------------------------------------------------ персоны (марафон)

  /** Персоны режима с ролью из `start.subject.roles` (как `GET /v1/modes/{key}/subjects`). */
  subjects(m: ModeOut, q: string, limit: number) {
    const roles = m.start?.subject?.roles ?? [];
    const byPerson = new Map<string, { name: string; role: string; items: number }>();
    for (const i of ITEMS) {
      if (i.domain !== m.domain || !roles.includes(i.person.role)) continue;
      const p = byPerson.get(i.person.name) ?? {
        name: i.person.name,
        role: i.person.role,
        items: 0,
      };
      p.items += 1;
      byPerson.set(i.person.name, p);
    }
    const needle = q.trim().toLowerCase();
    return [...byPerson.values()]
      .filter((p) => !needle || p.name.toLowerCase().includes(needle))
      .sort((a, b) => b.items - a.items || a.name.localeCompare(b.name))
      .slice(0, limit)
      .map((p) => ({ subject_id: personId(p.name), name: p.name, role: p.role, items: p.items }));
  }

  // ------------------------------------------------------------------ старт

  startSession(
    subject: string,
    m: ModeOut,
    presetId: string | null,
    subjectIds: string[] = [],
    collection: { id: string; version: number; itemIds: string[] } | null = null,
  ): MockSession {
    const pool = ITEMS.filter((i) => i.domain === m.domain);
    const session: MockSession = {
      id: this.nextId(1),
      subject,
      mode: m,
      day: this.isScheduled(m) ? this.today() : null,
      version: 0,
      status: "active",
      current: 0,
      rounds: [],
      lives: LIVES[m.source_type] ?? null,
      collection: collection ? { id: collection.id, version: collection.version } : null,
    };
    if (collection) {
      session.rounds = collection.itemIds.map((id, k) => blank(k, id));
    } else if (m.source_type === "daily") {
      session.rounds = (DAILY_SETS[m.key] ?? []).map((id, i) => blank(i, id));
    } else if (m.source_type === "marathon") {
      const names = new Set(subjectIds);
      session.rounds = pool
        .filter((i) => names.has(personId(i.person.name)))
        .sort((a, b) => a.year - b.year)
        .map((i, k) => blank(k, i.id));
    } else if (m.source_type === "pair") {
      // Цепочка: опорная единица раунда — загаданная прошлого.
      session.rounds = pool.slice(1).map((target, k) => {
        const r = blank(k, target.id);
        r.shown = [pool[k]!.id];
        r.options = [
          { option_id: "higher", label_key: "game.options.higher", itemId: null },
          { option_id: "lower", label_key: "game.options.lower", itemId: null },
        ];
        return r;
      });
    } else if (m.source_type === "bracket") {
      const seeds = pool.slice(0, BRACKET_SIZE);
      session.rounds = Array.from({ length: BRACKET_SIZE - 1 }, (_, k) => blank(k, ""));
      for (let k = 0; k < BRACKET_SIZE / 2; k++)
        this.fillPair(session.rounds[k]!, seeds[2 * k]!.id, seeds[2 * k + 1]!.id, BRACKET_SIZE);
    } else {
      const filter = presetId ? PRESET_FILTERS[presetId] : undefined;
      const picked = pool.filter((i) => !filter || filter(i));
      session.rounds = Array.from({ length: m.rounds }, (_, k) =>
        blank(k, picked[k % picked.length]!.id),
      );
    }
    this.sessions.set(session.id, session);
    return session;
  }

  private fillPair(r: Round, a: string, b: string, stage: number): void {
    r.itemId = a;
    r.stage = stage;
    r.shown = [a, b];
    r.options = [
      { option_id: "a", label_key: null, itemId: a },
      { option_id: "b", label_key: null, itemId: b },
    ];
  }

  // ------------------------------------------------------------------ ходы

  lostLives(s: MockSession, untilIndex = Infinity): number {
    return s.rounds.filter((r) => r.index <= untilIndex && r.outcome && LOST.has(r.outcome)).length;
  }

  private closeRound(s: MockSession, r: Round, outcome: NonNullable<Round["outcome"]>): void {
    r.status = outcome === "void" ? "void" : "finished";
    r.outcome = outcome;
    const streak = s.mode.presentation === "streak";
    const bracket = s.mode.presentation === "bracket";
    if (bracket) r.points = 0;
    else if (outcome === "exact") r.points = streak ? 1 : (POINTS[r.level - 1] ?? 0);
    else if (outcome === "partial" && r.partialLevel)
      r.points = Math.floor((POINTS[r.partialLevel - 1] ?? 0) * PARTIAL_RATIO);
    else r.points = 0;
    const outOfLives = s.lives !== null && this.lostLives(s) >= s.lives;
    const next = s.rounds.find((x) => x.index === r.index + 1);
    if (next && !outOfLives) {
      if (bracket && next.options.length === 0) this.advanceBracket(s, r, next);
      next.status = "active";
      s.current = next.index;
    } else {
      // Ранний конец (жизни кончились): невыданный план отбрасывается (D4).
      s.rounds = s.rounds.filter((x) => x.status !== "pending");
      s.status = "finished";
      this.saveRecord(s);
      this.markCompleted(s);
    }
  }

  /**
   * Следующий тур сетки целиком: победители завершённого тура парами по порядку
   * (`ProgressiveSource.followups`). Вызывается, когда сыгран последний раунд тура.
   */
  private advanceBracket(s: MockSession, closed: Round, next: Round): void {
    const winners = s.rounds
      .filter((x) => x.stage === closed.stage)
      .map((x) => x.picked ?? x.itemId);
    const stage = winners.length;
    for (let k = 0; k < stage / 2; k++) {
      const round = s.rounds.find((x) => x.index === next.index + k);
      if (round) this.fillPair(round, winners[2 * k]!, winners[2 * k + 1]!, stage);
    }
  }

  /** «Пройден» (как consumer `GameFinished` в collections): только пользователь и не автор. */
  private markCompleted(s: MockSession): void {
    const c = s.collection ? this.ugc.find(s.collection.id) : undefined;
    if (!c || !s.subject.startsWith("user.") || this.ugc.isAuthor(c, s.subject)) return;
    if (c.version !== s.collection!.version) return;
    if (!c.completed.has(s.subject)) c.plays += 1;
    c.completed.add(s.subject);
  }

  private saveRecord(s: MockSession): void {
    if (!s.subject.startsWith("user.") || s.mode.presentation === "bracket") return;
    const key = `${s.subject}:${s.mode.key}`;
    const best = this.records.get(key);
    const score = this.score(s);
    if (best === undefined || score > best) this.records.set(key, score);
  }

  private advanceLevel(s: MockSession, r: Round): void {
    if (r.level >= s.mode.levels.length) {
      this.closeRound(s, r, r.partialLevel ? "partial" : "miss");
    } else {
      r.level += 1;
    }
  }

  guess(
    s: MockSession,
    r: Round,
    answer: { media_item_id?: string; text?: string; option_id?: string },
  ): "exact" | "partial" | "miss" {
    if (answer.option_id !== undefined) return this.pick(s, r, answer.option_id);
    const target = itemById(r.itemId)!;
    const picked = answer.media_item_id ? itemById(answer.media_item_id) : undefined;
    const display = picked ? `${picked.title}` : (answer.text ?? "");
    let result: "exact" | "partial" | "miss";
    if (picked) {
      result =
        picked.id === target.id
          ? "exact"
          : s.mode.domain === "music" && picked.person.name === target.person.name
            ? "partial"
            : "miss";
    } else {
      result = display.trim().toLowerCase() === target.title.toLowerCase() ? "exact" : "miss";
    }
    r.guesses.push({ kind: picked ? "item" : "text", display, result });
    if (result === "exact") this.closeRound(s, r, "exact");
    else {
      if (result === "partial" && r.partialLevel === null) r.partialLevel = r.level;
      this.advanceLevel(s, r);
    }
    s.version += 1;
    return result;
  }

  /** Ответ выбором (`pick`): сравнение метрик (пара) или предпочтение (сетка — всегда верно). */
  private pick(s: MockSession, r: Round, optionId: string): "exact" | "miss" {
    const option = r.options.find((o) => o.option_id === optionId)!;
    let result: "exact" | "miss" = "exact";
    let display = option.label_key ?? optionId;
    if (option.itemId) {
      r.picked = option.itemId;
      display = itemById(option.itemId)?.title ?? optionId;
    } else {
      const shown = itemById(r.shown[0]!)!;
      const target = itemById(r.itemId)!;
      const higher = metricOf(target) >= metricOf(shown);
      result = (optionId === "higher") === higher ? "exact" : "miss";
    }
    r.guesses.push({ kind: "option", display, result });
    this.closeRound(s, r, result);
    s.version += 1;
    return result;
  }

  /** Доля согласных с выбором (сетка): детерминированно по раунду. */
  agreePct(r: Round): number {
    return 35 + ((r.index * 17) % 50);
  }

  skip(s: MockSession, r: Round): void {
    r.guesses.push({ kind: "skip", display: "", result: null });
    this.advanceLevel(s, r);
    s.version += 1;
  }

  giveUp(s: MockSession, r: Round): void {
    this.closeRound(s, r, "gave_up");
    s.version += 1;
  }

  voidRound(s: MockSession, r: Round): void {
    this.closeRound(s, r, "void");
    s.version += 1;
  }

  // ------------------------------------------------------------------ ассеты

  private register(asset: Asset, seed: number, ext: string): string {
    const hex = (this.seq * 7919 + seed * 104729 + this.assets.size).toString(16).padStart(16, "0");
    const path = `/media/a/${hex}.${ext}`;
    this.assets.set(path, asset);
    const expires = Math.floor(this.now().getTime() / 1000) + 60;
    return `${path}?md5=mock&expires=${expires}`;
  }

  assetUrl(kind: "audio" | "image", seconds: number, blur: number, seed: number): string {
    return this.register({ kind, seconds, blur, seed }, seed, kind === "audio" ? "wav" : "svg");
  }

  /** Ссылка, которая уже не работает (подпись истекла): файл по ней — 403. */
  staleUrl(kind: "audio" | "image", seed: number): string {
    const expires = Math.floor(this.now().getTime() / 1000) - 1;
    return `/media/a/stale${seed}.${kind === "audio" ? "wav" : "svg"}?md5=mock&expires=${expires}`;
  }

  /** JSON-ассет текстовой загадки уровня (`text_asset.v1`). */
  textAsset(m: ModeOut, item: MockItem, level: number): unknown {
    const n = Number(item.id.slice(-3));
    if (m.clue_type === "emoji") {
      const count = EMOJI_COUNTS[level - 1];
      const all = EMOJI[item.genres[0] ?? ""] ?? ["🎬"];
      return {
        version: 1,
        blocks: [{ kind: "emoji", text: all.slice(0, count ?? undefined).join("") }],
      };
    }
    const fields = DESCRIPTION_FIELDS.slice(0, level).flat();
    const blocks: unknown[] = [];
    const items: { field: string; value: string }[] = [];
    for (const f of fields) {
      if (f === "tagline") blocks.push({ kind: "tagline", text: `Синтетическая история №${n}` });
      else if (f === "plot_masked")
        blocks.push({ kind: "plot", text: `Герой ███ отправляется туда, откуда не возвращаются.` });
      else if (f === "year") items.push({ field: "year", value: String(item.year) });
      else if (f === "genres") items.push({ field: "genres", value: item.genres.join(", ") });
      else if (f === "country") items.push({ field: "country", value: "СССР" });
      else if (f === "director") items.push({ field: "director", value: item.person.name });
      else if (f === "cast_top3") items.push({ field: "cast_top3", value: "А. Актёр, Б. Актриса" });
    }
    if (items.length) blocks.push({ kind: "fields", items });
    return { version: 1, blocks };
  }

  /** Выдача ассета уровня (`POST .../clue`). */
  levelClue(s: MockSession, r: Round, level: number) {
    const hint = s.mode.levels[level - 1]?.hint ?? {};
    const kind = clueKindOf(s.mode);
    const expires_at = new Date(this.now().getTime() + 60_000).toISOString();
    if (kind === "text") {
      const item = itemById(r.itemId)!;
      return {
        level,
        kind,
        url: this.register(
          { kind: "text", body: this.textAsset(s.mode, item, level) },
          r.index + 1,
          "json",
        ),
        expires_at,
        mime: "application/json",
        meta:
          s.mode.clue_type === "emoji"
            ? { count: (hint.count as number | null) ?? null }
            : { fields: (hint.fields as string[]) ?? [] },
      };
    }
    const seconds = Number(hint.seconds ?? 0);
    const blur = Number(hint.blur_px ?? 0);
    // Последовательность кадров: каждый уровень — другой кадр (другой «сид» картинки).
    const seed = r.index + 1 + (hint.frame ? Number(hint.frame) * 11 : 0);
    // Поля meta — hint_fields загадки ядра (как отдаёт game).
    const meta =
      kind === "audio"
        ? { seconds }
        : hint.frame
          ? { frame: Number(hint.frame) }
          : { blur_px: blur, crop: hint.crop == null ? null : Number(hint.crop) };
    return {
      level,
      kind,
      url: this.assetUrl(kind, seconds, blur, seed),
      expires_at,
      mime: kind === "audio" ? "audio/wav" : "image/svg+xml",
      meta,
    };
  }

  finalClue(s: MockSession, r: Round, fresh = false) {
    const kind = revealKindOf(s.mode);
    const stale = this.scenario.staleRevealLinks && !fresh;
    return {
      kind,
      url: stale ? this.staleUrl(kind, r.index + 1) : this.assetUrl(kind, 15, 0, r.index + 1),
      expires_at: new Date(this.now().getTime() + 60_000).toISOString(),
      mime: kind === "audio" ? "audio/wav" : "image/svg+xml",
    };
  }

  /** Свежая ссылка ревила (`POST .../rounds/{i}/final-clue`). */
  freshFinalClue(s: MockSession, r: Round) {
    this.finalClueCalls += 1;
    return { level: r.level, ...this.finalClue(s, r, true), meta: {} };
  }

  reveal(s: MockSession, r: Round) {
    const item = itemById(r.itemId)!;
    return {
      media_item_id: item.id,
      title: item.title,
      year: item.year,
      persons: [{ name: item.person.name, role: item.person.role }],
      metric: s.mode.source_type === "pair" ? metricOf(item) : null,
      final_clue: this.finalClue(s, r),
    };
  }

  // ------------------------------------------------------------------ представления

  private shownOut(s: MockSession, r: Round) {
    return r.shown.map((id) => {
      const item = itemById(id)!;
      const option = r.options.find((o) => o.itemId === id);
      return {
        option_id: option?.option_id ?? null,
        title: item.title,
        year: item.year,
        // Метрика — только у опорной единицы пары (без варианта).
        metric: s.mode.source_type === "pair" && !option ? metricOf(item) : null,
        clue: {
          kind: "image" as const,
          url: this.assetUrl("image", 0, 0, Number(id.slice(-3))),
          expires_at: new Date(this.now().getTime() + 60_000).toISOString(),
          mime: "image/svg+xml",
        },
      };
    });
  }

  private progress(s: MockSession, r: Round): RoundOut["progress"] {
    const closed = r.status === "finished" || r.status === "void";
    const until = closed ? r.index : r.index - 1;
    const streakOf = () => {
      let n = 0;
      for (const x of s.rounds.filter((x) => x.index <= until))
        n = x.outcome === "exact" ? n + 1 : 0;
      return n;
    };
    return {
      lives_left: s.lives === null ? null : Math.max(0, s.lives - this.lostLives(s, until)),
      streak: s.mode.presentation === "streak" ? streakOf() : null,
      stage: r.stage,
    };
  }

  roundOut(s: MockSession, r: Round): RoundOut {
    const closed = r.status === "finished" || r.status === "void";
    const pending = r.status === "pending";
    return {
      index: r.index,
      status: r.status,
      level: r.level,
      levels_count: s.mode.levels.length,
      guesses: r.guesses,
      points: closed ? r.points : null,
      reveal: closed && r.status !== "void" ? this.reveal(s, r) : null,
      // Невыданный раунд — без состава (D4).
      shown: pending ? [] : this.shownOut(s, r),
      options: pending
        ? []
        : r.options.map((o) => ({
            option_id: o.option_id,
            label_key: o.label_key,
            title: o.itemId ? (itemById(o.itemId)?.title ?? null) : null,
          })),
      progress: pending ? null : this.progress(s, r),
    };
  }

  maxScore(s: MockSession): number {
    if (s.mode.presentation === "bracket") return 0;
    if (s.mode.presentation === "streak") return this.score(s);
    return s.rounds.filter((r) => r.status !== "void").length * (POINTS[0] ?? 0);
  }

  score(s: MockSession): number {
    if (s.mode.presentation === "streak") {
      let n = 0;
      for (const r of s.rounds) if (r.outcome) n = r.outcome === "exact" ? n + 1 : 0;
      return n;
    }
    return s.rounds.reduce((acc, r) => acc + (r.points ?? 0), 0);
  }

  snapshot(s: MockSession): SessionSnapshot {
    return {
      id: s.id,
      version: s.version,
      status: s.status,
      mode: {
        key: s.mode.key,
        version: s.mode.version,
        domain: s.mode.domain,
        clue_type: s.mode.clue_type,
      },
      day: s.day,
      score: this.score(s),
      max_score: this.maxScore(s),
      current_round: s.current,
      rounds: s.rounds.map((r) => this.roundOut(s, r)),
    };
  }

  shareText(s: MockSession): string {
    const cell = (r: Round) =>
      r.outcome === "exact"
        ? "🟩"
        : r.outcome === "partial"
          ? "🟨"
          : r.outcome === "void"
            ? "⬜"
            : "🟥";
    const lines = s.rounds.map((r) => `${cell(r)} ${r.outcome === "exact" ? r.level : "—"}`);
    return `OnCue ${s.mode.key} ${s.day ?? ""}\n${this.score(s)}/${this.maxScore(s)}\n${lines.join("\n")}`;
  }

  record(s: MockSession) {
    if (!s.subject.startsWith("user.") || s.mode.presentation === "bracket") return null;
    const best = this.records.get(`${s.subject}:${s.mode.key}`) ?? this.score(s);
    return { best, is_new: this.score(s) >= best && this.score(s) > 0, percentile: 72.5 };
  }

  bracket(s: MockSession) {
    if (s.mode.presentation !== "bracket") return null;
    const title = (id: string) => itemById(id)?.title ?? "";
    const picks = s.rounds
      .filter((r) => r.picked && r.stage)
      .map((r) => ({
        stage: r.stage!,
        winner: title(r.picked!),
        loser: title(r.shown.find((id) => id !== r.picked) ?? ""),
      }));
    return picks.length ? { champion: picks.at(-1)!.winner, picks } : null;
  }

  result(s: MockSession): SessionResult {
    return {
      presentation: s.mode.presentation ?? "score",
      score: this.score(s),
      max_score: this.maxScore(s),
      rounds: s.rounds.map((r) => ({
        index: r.index,
        outcome: r.outcome ?? "miss",
        level: r.outcome === "exact" || r.outcome === "partial" ? r.level : null,
        points: r.points ?? 0,
        reveal: r.status === "void" ? null : this.reveal(s, r),
      })),
      share: { text: this.shareText(s) },
      daily_stats:
        s.day && s.mode.source_type === "daily"
          ? s.rounds.map((r) => ({ index: r.index, exact_pct: DAILY_EXACT_PCT[r.index] ?? 0 }))
          : null,
      record: this.record(s),
      bracket: this.bracket(s),
      collection: s.collection,
    };
  }

  dailyStatus(subject: string | null, m: ModeOut): DailyStatus {
    const s = subject ? this.dailyFor(subject, m.key) : undefined;
    return {
      mode_key: m.key,
      day: this.today(),
      status: !s ? "not_started" : s.status === "finished" ? "finished" : "in_progress",
      session_id: s?.id ?? null,
      score: s && s.status === "finished" ? this.score(s) : null,
      max_score: m.presentation === "bracket" ? 0 : m.rounds * (POINTS[0] ?? 0),
      next_reset_at: WEEKLY.has(m.source_type) ? this.nextWeekResetAt() : this.nextResetAt(),
    };
  }

  /** Плановые режимы: `weekly` — сетка (bff кладёт её в блок `weekly`), иначе — дейлики. */
  dailyCards(subject: string | null, weekly = false): DailyCard[] {
    return MODES.filter((m) => this.isScheduled(m) && WEEKLY.has(m.source_type) === weekly).map(
      (m) => ({
        ...this.dailyStatus(subject, m),
        domain: m.domain,
        title_key: m.title_key,
        presentation: m.presentation ?? "score",
      }),
    );
  }

  modeCards(filter: (m: ModeOut) => boolean = () => true) {
    return MODES.filter(filter).map((m) => ({
      key: m.key,
      domain: m.domain,
      title_key: m.title_key,
      source_type: m.source_type,
      clue_type: m.clue_type,
      rounds: m.rounds,
      allow_guest: m.allow_guest,
      presets: m.presets ?? null,
      answer_format: m.answer_format ?? "classic",
      presentation: m.presentation ?? "score",
      start: m.start ?? null,
    }));
  }

  /** Витрина bff: неплановые режимы группами по `(source_type, presentation)`. */
  modeGroups(domain: string | null) {
    const groups = new Map<string, ReturnType<MockWorld["modeCards"]>>();
    for (const card of this.modeCards(
      (m) => !this.isScheduled(m) && (!domain || m.domain === domain),
    )) {
      const key = `${card.source_type}|${card.presentation}`;
      groups.set(key, [...(groups.get(key) ?? []), card]);
    }
    return [...groups.entries()].map(([key, modes]) => {
      const [source_type, presentation] = key.split("|") as [
        string,
        "score" | "streak" | "bracket",
      ];
      return { source_type, presentation, modes };
    });
  }

  /** Личные рекорды (bff `records`): только пользователю. */
  recordCards(subject: string) {
    return [...this.records.entries()]
      .filter(([key]) => key.startsWith(`${subject}:`))
      .map(([key, best]) => {
        const m = this.mode(key.slice(subject.length + 1))!;
        return {
          mode_key: m.key,
          domain: m.domain,
          title_key: m.title_key,
          presentation: m.presentation ?? "score",
          best,
          percentile: 72.5,
          achieved_at: this.now().toISOString(),
        };
      });
  }

  resultsPage(s: MockSession): ResultsPage {
    const res = this.result(s);
    const ids = [...new Set(s.rounds.map((r) => r.itemId).filter(Boolean))];
    return {
      blocks: {
        result: {
          status: "ok",
          data: {
            session_id: s.id,
            mode_key: s.mode.key,
            score: res.score,
            max_score: res.max_score,
            share_text: res.share.text,
            presentation: res.presentation ?? "score",
            record: res.record ?? null,
            bracket: res.bracket ?? null,
            rounds: res.rounds.map((r) => ({
              index: r.index,
              outcome: r.outcome,
              level: r.level,
              points: r.points,
              media_item_id: r.reveal?.media_item_id ?? null,
              exact_pct: res.daily_stats?.find((d) => d.index === r.index)?.exact_pct ?? null,
            })),
          },
        },
        items: {
          status: "ok",
          data: ids.map((id) => {
            const i = itemById(id)!;
            return {
              media_item_id: i.id,
              domain: i.domain,
              kind: i.domain === "music" ? "track" : "film",
              title: i.title,
              original_title: null,
              year: i.year,
              genres: i.genres,
              persons: [{ name: i.person.name, role: i.person.role }],
              external_links: [],
            };
          }),
        },
      },
    };
  }

  autocomplete(q: string, domain: string | null, limit: number): Suggestion[] {
    const needle = q.trim().toLowerCase();
    if (!needle) return [];
    return ITEMS.filter(
      (i) =>
        (!domain || i.domain === domain) &&
        (i.title.toLowerCase().includes(needle) || i.person.name.toLowerCase().includes(needle)),
    )
      .slice(0, limit)
      .map((i) => ({
        id: i.id,
        domain: i.domain,
        title: i.title,
        year: i.year,
        subtitle: i.person.name,
      }));
  }

  /** Ассет по пути ссылки (для обработчика `/media/a/...`). */
  asset(path: string): Asset | undefined {
    return this.assets.get(path);
  }
}
