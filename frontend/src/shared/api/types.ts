/** Короткие имена схем API (типы сгенерированы из contracts/openapi, см. `pnpm gen:api`). */
import type { components as Game } from "./schema/game";
import type { components as Bff } from "./schema/bff_web";
import type { components as Catalog } from "./schema/catalog";
import type { components as Identity } from "./schema/identity";

type G = Game["schemas"];
type B = Bff["schemas"];

export type Problem = G["Problem"];
export type SessionSnapshot = G["SessionSnapshot"];
export type RoundOut = G["RoundOut"];
export type GuessOut = G["GuessOut"];
export type Reveal = G["Reveal"];
export type ClueOut = G["ClueOut"];
export type ClueKind = ClueOut["kind"];
export type MoveResult = G["MoveResult"];
export type ModeOut = G["ModeOut"];
export type LevelOut = G["LevelOut"];
export type SessionResult = G["SessionResult"];
export type DailyStatus = G["DailyStatus"];
export type Answer = G["GuessRequest"]["answer"];
export type Domain = ModeOut["domain"];

export type HomePage = B["HomePage"];
export type SoloPage = B["SoloPage"];
export type ResultsPage = B["ResultsPage"];
export type ResultSummary = B["ResultSummary"];
export type ResultRoundCard = B["ResultRoundCard"];
export type ItemCard = B["ItemCard"];
export type DailyCard = B["DailyCard"];
export type ModeCard = B["ModeCard"];
export type SessionInfo = B["SessionInfo"];

export type Suggestion = Catalog["schemas"]["SuggestionOut"];
export type LoginRequest = Identity["schemas"]["LoginRequest"];
export type RegisterRequest = Identity["schemas"]["RegisterRequest"];
