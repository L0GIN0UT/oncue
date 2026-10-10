/**
 * Состояние мок-бэкенда по контрактам (contracts/openapi/{game,bff_web,catalog,identity}.json).
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
import { dayIn, nextMidnight } from "@/features/daily/countdown";

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
];

export const itemById = (id: string) => ITEMS.find((i) => i.id === id);

const AUDIO_LEVELS = [0.5, 1, 2, 4, 8, 15].map((seconds, i) => ({
  index: i + 1,
  hint: { seconds },
}));
const FRAME_LEVELS = [48, 32, 20, 12, 6, 0].map((blur_px, i) => ({
  index: i + 1,
  hint: { blur_px, crop: [0.35, 0.5, 0.7, 1, 1, 1][i] ?? 1 },
}));
const POINTS = [6, 5, 4, 3, 2, 1];
const PARTIAL_RATIO = 0.5;

export const MODES: ModeOut[] = [
  {
    key: "music.daily.classic",
    version: 1,
    domain: "music",
    title_key: "modes.music.daily.classic.title",
    source_type: "daily",
    clue_type: "audio_intro",
    rounds: 5,
    levels: AUDIO_LEVELS,
    allow_guest: true,
    presets: null,
  },
  {
    key: "film.daily.frame",
    version: 1,
    domain: "film",
    title_key: "modes.film.daily.frame.title",
    source_type: "daily",
    clue_type: "frame_progressive",
    rounds: 5,
    levels: FRAME_LEVELS,
    allow_guest: true,
    presets: null,
  },
  {
    key: "music.random.classic",
    version: 1,
    domain: "music",
    title_key: "modes.music.random.classic.title",
    source_type: "random",
    clue_type: "audio_intro",
    rounds: 10,
    levels: AUDIO_LEVELS,
    allow_guest: true,
    presets: [
      { id: "y2000s", title_key: "modes.presets.y2000s" },
      { id: "y2010s", title_key: "modes.presets.y2010s" },
      { id: "rock", title_key: "modes.presets.rock" },
    ],
  },
  {
    key: "film.random.frame",
    version: 1,
    domain: "film",
    title_key: "modes.film.random.frame.title",
    source_type: "random",
    clue_type: "frame_progressive",
    rounds: 10,
    levels: FRAME_LEVELS,
    allow_guest: true,
    presets: [
      { id: "y1990s", title_key: "modes.presets.y1990s" },
      { id: "y2000s", title_key: "modes.presets.y2000s" },
      { id: "comedy", title_key: "modes.presets.comedy" },
    ],
  },
];

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

type Round = {
  index: number;
  itemId: string;
  status: RoundOut["status"];
  level: number;
  guesses: RoundOut["guesses"];
  partialLevel: number | null;
  outcome: "exact" | "partial" | "miss" | "gave_up" | "void" | null;
  points: number | null;
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
};

export class MockWorld {
  sessions = new Map<string, MockSession>();
  idempotency = new Map<string, { status: number; body: unknown }>();
  users: MockUser[] = [
    {
      id: uuid(901),
      nickname: "player",
      email: "player@example.com",
      password: "correct-horse",
      verified: true,
    },
  ];
  scenario: Scenario = {};
  /** Журнал выданных ссылок на ассеты: путь → параметры (для отдачи «файла»). */
  assets = new Map<
    string,
    { kind: "audio" | "image"; seconds: number; blur: number; seed: number }
  >();
  private seq = 0;
  now: () => Date = () => new Date();

  reset(): void {
    this.sessions.clear();
    this.idempotency.clear();
    this.assets.clear();
    this.scenario = {};
    this.users = this.users.slice(0, 1);
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

  mode(key: string): ModeOut | undefined {
    return MODES.find((m) => m.key === key);
  }

  dailyFor(subject: string, modeKey: string): MockSession | undefined {
    const day = this.today();
    return [...this.sessions.values()].find(
      (s) => s.subject === subject && s.mode.key === modeKey && s.day === day,
    );
  }

  startSession(subject: string, mode: ModeOut, presetId: string | null): MockSession {
    let ids: string[];
    if (mode.source_type === "daily") {
      ids = DAILY_SETS[mode.key] ?? [];
    } else {
      const filter = presetId ? PRESET_FILTERS[presetId] : undefined;
      const pool = ITEMS.filter((i) => i.domain === mode.domain && (!filter || filter(i)));
      ids = Array.from({ length: mode.rounds }, (_, k) => pool[k % pool.length]!.id);
    }
    const session: MockSession = {
      id: this.nextId(1),
      subject,
      mode,
      day: mode.source_type === "daily" ? this.today() : null,
      version: 0,
      status: "active",
      current: 0,
      rounds: ids.map((itemId, index) => ({
        index,
        itemId,
        status: index === 0 ? "active" : "pending",
        level: 1,
        guesses: [],
        partialLevel: null,
        outcome: null,
        points: null,
      })),
    };
    this.sessions.set(session.id, session);
    return session;
  }

  // ------------------------------------------------------------------ ходы

  private closeRound(s: MockSession, r: Round, outcome: NonNullable<Round["outcome"]>): void {
    r.status = outcome === "void" ? "void" : "finished";
    r.outcome = outcome;
    if (outcome === "exact") r.points = POINTS[r.level - 1] ?? 0;
    else if (outcome === "partial" && r.partialLevel)
      r.points = Math.floor((POINTS[r.partialLevel - 1] ?? 0) * PARTIAL_RATIO);
    else r.points = 0;
    const next = s.rounds.find((x) => x.index === r.index + 1);
    if (next) {
      next.status = "active";
      s.current = next.index;
    } else {
      s.status = "finished";
    }
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
    answer: { media_item_id?: string; text?: string },
  ): "exact" | "partial" | "miss" {
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

  // ------------------------------------------------------------------ представления

  assetUrl(kind: "audio" | "image", seconds: number, blur: number, seed: number): string {
    const hex = (this.seq * 7919 + seed * 104729 + this.assets.size).toString(16).padStart(16, "0");
    const path = `/media/a/${hex}.${kind === "audio" ? "wav" : "svg"}`;
    this.assets.set(path, { kind, seconds, blur, seed });
    const expires = Math.floor(this.now().getTime() / 1000) + 60;
    return `${path}?md5=mock&expires=${expires}`;
  }

  finalClue(s: MockSession, r: Round) {
    const kind = s.mode.domain === "music" ? ("audio" as const) : ("image" as const);
    return {
      kind,
      url: this.assetUrl(kind, 15, 0, r.index + 1),
      expires_at: new Date(this.now().getTime() + 60_000).toISOString(),
      mime: kind === "audio" ? "audio/wav" : "image/svg+xml",
    };
  }

  reveal(s: MockSession, r: Round) {
    const item = itemById(r.itemId)!;
    return {
      media_item_id: item.id,
      title: item.title,
      year: item.year,
      persons: [{ name: item.person.name, role: item.person.role }],
      final_clue: this.finalClue(s, r),
    };
  }

  roundOut(s: MockSession, r: Round): RoundOut {
    const closed = r.status === "finished" || r.status === "void";
    return {
      index: r.index,
      status: r.status,
      level: r.level,
      levels_count: s.mode.levels.length,
      guesses: r.guesses,
      points: closed ? r.points : null,
      reveal: closed && r.status !== "void" ? this.reveal(s, r) : null,
    };
  }

  maxScore(s: MockSession): number {
    return s.rounds.filter((r) => r.status !== "void").length * (POINTS[0] ?? 0);
  }

  score(s: MockSession): number {
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

  result(s: MockSession): SessionResult {
    return {
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
      daily_stats: s.day
        ? s.rounds.map((r) => ({ index: r.index, exact_pct: DAILY_EXACT_PCT[r.index] ?? 0 }))
        : null,
    };
  }

  dailyStatus(subject: string | null, mode: ModeOut): DailyStatus {
    const s = subject ? this.dailyFor(subject, mode.key) : undefined;
    return {
      mode_key: mode.key,
      day: this.today(),
      status: !s ? "not_started" : s.status === "finished" ? "finished" : "in_progress",
      session_id: s?.id ?? null,
      score: s && s.status === "finished" ? this.score(s) : null,
      max_score: mode.rounds * (POINTS[0] ?? 0),
      next_reset_at: this.nextResetAt(),
    };
  }

  dailyCards(subject: string | null): DailyCard[] {
    return MODES.filter((m) => m.source_type === "daily").map((m) => ({
      ...this.dailyStatus(subject, m),
      domain: m.domain,
      title_key: m.title_key,
    }));
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
    }));
  }

  resultsPage(s: MockSession): ResultsPage {
    const res = this.result(s);
    const ids = [...new Set(s.rounds.map((r) => r.itemId))];
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
}
