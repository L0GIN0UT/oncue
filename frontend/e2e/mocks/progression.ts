/**
 * Мок этапа 5a по контрактам `contracts/openapi/{progression,bff_web}.json`: экран профиля
 * `/pages/profile`, блок `progress` главной и спин колеса `POST /api/progression/v1/wheel/spin`.
 * Фикстуры типизированы сгенерированными типами; тексты (задания, бейджи, сектора) — «из конфига
 * progression», как у настоящего сервиса.
 *
 * Сценарии (`POST /__mock/scenario`): `progressDown` — имена блоков профиля (`wheel`, `wallet`…)
 * и/или `home`, которые гаснут; `spinConflict` — спин отвечает 409 `progression.wheel_disabled`;
 * `spinNetworkOnce` — первый спин обрывается; `spunElsewhere` — игрок уже крутил в другой вкладке
 * (первый спин вернёт `repeated: true`).
 */
import { http, HttpResponse, type HttpHandler } from "msw";
import type {
  BadgeCard,
  HomeProgressCard,
  PlayerPage,
  PrizeCard,
  ProfilePage,
  PublicProgressCard,
  ProgressCard,
  QuestsCard,
  SpinCard,
  SpinResult,
  WalletCard,
  WheelCard,
  WheelSectorCard,
} from "../../src/shared/api/types";
import { csrfOk, problem, subjectOf } from "./handlers";
import { economyProfileBlocks, itemByKey } from "./economy";
import { isRestricted, playerSocialBlock } from "./social";
import { eventsArchive } from "./events";
import type { MockWorld } from "./world";

const NO_STORE = { "Cache-Control": "private, no-store" };
const NO_PRIZE: PrizeCard = { kind: "none", amount: 0 };

export const PROGRESS: ProgressCard = {
  level: 7,
  max_level: 100,
  total_xp: 3_700,
  xp_in_level: 120,
  xp_to_next: 380,
  solo_xp_today: 300,
  solo_xp_cap: 2_000,
  streaks: [
    { domain: "music", current: 3, best: 9 },
    { domain: "film", current: 5, best: 5 },
  ],
  boost: { multiplier: 1.5, matches_left: 2 },
};

export const WALLET: WalletCard = { earned: 40, paid: 0, total: 40 };

export const QUESTS: QuestsCard = {
  daily: [
    {
      key: "play_three",
      title: "Сыграй три раунда",
      description: "В любом режиме",
      target: 3,
      progress: 3,
      completed: true,
      reward_xp: 50,
      reward_verses: 5,
    },
    {
      key: "win_duel",
      title: "Выиграй дуэль",
      description: "Онлайн 1×1",
      target: 1,
      progress: 0,
      completed: false,
      reward_xp: 80,
      reward_verses: 5,
    },
  ],
  weekly: [
    {
      key: "streak_week",
      title: "Неделя дейликов",
      description: "Пройди дейлик 7 дней",
      target: 7,
      progress: 2,
      completed: false,
      reward_xp: 300,
      reward_verses: 20,
    },
  ],
};

export const BADGES: BadgeCard[] = [
  {
    key: "first_win",
    title: "Первая победа",
    description: "Выиграй дуэль",
    target: 1,
    progress: 1,
    unlocked_at: "2026-10-20T12:00:00Z",
  },
  {
    key: "streak_7",
    title: "Неделя подряд",
    description: "Серия дейлика 7 дней",
    target: 7,
    progress: 3,
    unlocked_at: null,
  },
];

/** Чужие игроки для `/players/{id}` (публичный прогресс). */
export const PLAYERS: PublicProgressCard[] = [
  {
    user_id: "00000000-0000-4000-8000-000000000902",
    level: 12,
    total_xp: 7_850,
    badges: [
      { key: "first_win", title: "Первая победа", unlocked_at: "2026-10-01T12:00:00Z" },
      { key: "streak_7", title: "Неделя подряд", unlocked_at: "2026-10-15T09:30:00Z" },
    ],
  },
];

export const SECTORS: WheelSectorCard[] = [
  { key: "verses_5", title: "5 Версов", chance: 0.3, prize: { kind: "verses", amount: 5 } },
  {
    key: "boost_15",
    title: "Буст ×1,5",
    chance: 0.2,
    prize: { kind: "boost", amount: 0, multiplier: 1.5, matches: 3 },
  },
  { key: "empty", title: "Пусто", chance: 0.5, prize: NO_PRIZE },
];

/** Какой сектор «выпадает» в моке: первый — детерминированно, без случайности. */
export const SPIN_SECTOR = SECTORS[0]!;

export class ProgressionWorld {
  /** Сегодняшний спин по субъектам. */
  spun = new Map<string, SpinCard>();
  spinCalls = 0;
  private networkFailed = false;

  reset(): void {
    this.spun.clear();
    this.spinCalls = 0;
    this.networkFailed = false;
  }

  /** Сообщить, что сетевой сбой «первого спина» уже был. */
  takeNetworkFailure(): boolean {
    if (this.networkFailed) return false;
    this.networkFailed = true;
    return true;
  }
}

const down = (world: MockWorld, name: string) => world.scenario.progressDown?.includes(name);
const unavailable = { status: "unavailable" as const, data: null };

export function wheelCard(world: MockWorld, subject: string): WheelCard {
  const today = world.progression.spun.get(subject) ?? null;
  return {
    enabled: true,
    can_spin: today === null,
    next_spin_at: today ? world.nextResetAt() : null,
    sectors: SECTORS,
    today,
  };
}

export function profilePage(world: MockWorld, subject: string): ProfilePage {
  const block = <T>(name: string, data: T) =>
    down(world, name) ? unavailable : { status: "ok" as const, data };
  return {
    blocks: {
      progress: block("progress", PROGRESS),
      wallet: block("wallet", {
        ...WALLET,
        earned: world.economy.balance,
        total: world.economy.balance + WALLET.paid,
      }),
      quests: block("quests", QUESTS),
      badges: block("badges", BADGES),
      wheel: block("wheel", wheelCard(world, subject)),
      ...economyProfileBlocks(world),
      events: eventsArchive(world),
    },
  };
}

/** Блок `progress` главной: `null` — гость и аноним, как у bff. */
export function homeProgress(world: MockWorld, subject: string | null) {
  if (!subject?.startsWith("user.")) return null;
  if (down(world, "home")) return unavailable;
  const card: HomeProgressCard = {
    level: PROGRESS.level,
    xp_in_level: PROGRESS.xp_in_level,
    xp_to_next: PROGRESS.xp_to_next,
    best_streak: Math.max(...PROGRESS.streaks.map((s) => s.current)),
    can_spin: !world.progression.spun.has(subject),
    quests_open: QUESTS.daily.filter((q) => !q.completed).length,
  };
  return { status: "ok" as const, data: card };
}

/** Ответ progression: у `PrizeView` все поля обязательны (отсутствующие — `null`). */
function asResult(spin: SpinCard, repeated: boolean): SpinResult {
  const { kind, amount, multiplier, matches, item_key } = spin.prize;
  return {
    ...spin,
    repeated,
    prize: {
      kind,
      amount,
      multiplier: multiplier ?? null,
      matches: matches ?? null,
      item_key: item_key ?? null,
    },
  };
}

function spinOf(world: MockWorld, subject: string): SpinResult {
  const existing = world.progression.spun.get(subject);
  if (existing) return asResult(existing, true);
  const fresh: SpinCard = {
    day: world.today(),
    sector_key: SPIN_SECTOR.key,
    prize: SPIN_SECTOR.prize,
    spun_at: world.now().toISOString(),
    repeated: false,
  };
  world.progression.spun.set(subject, fresh);
  // Игрок крутил в другой вкладке: для этой вкладки результат — повтор.
  return asResult(fresh, Boolean(world.scenario.spunElsewhere));
}

export function createProgressionHandlers(world: MockWorld): HttpHandler[] {
  return [
    http.get("*/api/web/v1/pages/profile", ({ request }) => {
      const subject = subjectOf(request);
      if (!subject) return problem(401, "auth.unauthenticated");
      if (!subject.startsWith("user.")) return problem(403, "auth.guest_forbidden");
      return HttpResponse.json(profilePage(world, subject), { headers: NO_STORE });
    }),

    http.get("*/api/web/v1/pages/players/:id", ({ request, params }) => {
      const subject = subjectOf(request);
      if (!subject) return problem(401, "auth.unauthenticated");
      if (!subject.startsWith("user.")) return problem(403, "auth.guest_forbidden");
      const playerId = String(params.id);
      const player = PLAYERS.find((p) => p.user_id === playerId);
      // Недоступный профиль неотличим от закрытого: только оформление (5c).
      const restricted = isRestricted(world, playerId);
      if (!player && !restricted) return problem(404, "progression.user_not_found");
      const progress =
        down(world, "player") || restricted || !player
          ? unavailable
          : { status: "ok" as const, data: player };
      const appearance = down(world, "appearance")
        ? unavailable
        : {
            status: "ok" as const,
            data: {
              custom_avatar_url: null,
              slots: [{ slot: "title", item: itemByKey("champion") }],
            },
          };
      return HttpResponse.json(
        {
          blocks: { progress, appearance, social: playerSocialBlock(world, playerId) },
          restricted,
        } satisfies PlayerPage,
        {
          headers: NO_STORE,
        },
      );
    }),

    http.post("*/api/progression/v1/wheel/spin", ({ request }) => {
      const subject = subjectOf(request);
      if (!subject) return problem(401, "auth.unauthenticated");
      if (!subject.startsWith("user.")) return problem(403, "auth.guest_forbidden");
      if (!csrfOk(request)) return problem(403, "auth.csrf_failed");
      world.progression.spinCalls += 1;
      if (world.scenario.spinNetworkOnce && world.progression.takeNetworkFailure())
        return HttpResponse.error();
      if (world.scenario.spinConflict) return problem(409, "progression.wheel_disabled");
      return HttpResponse.json(spinOf(world, subject), { headers: NO_STORE });
    }),
  ];
}
