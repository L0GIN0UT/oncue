/** Короткие имена схем API (типы сгенерированы из contracts/openapi, см. `pnpm gen:api`). */
import type { components as Game } from "./schema/game";
import type { components as Bff } from "./schema/bff_web";
import type { components as Catalog } from "./schema/catalog";
import type { components as Identity } from "./schema/identity";
import type { components as Collections, operations as CollectionsOps } from "./schema/collections";
import type { components as Library } from "./schema/library";
import type { components as Match } from "./schema/match";

type G = Game["schemas"];
type B = Bff["schemas"];

export type Problem = G["Problem"];
export type SessionSnapshot = G["SessionSnapshot"];
export type RoundOut = G["RoundOut"];
export type GuessOut = G["GuessOut"];
export type Reveal = G["Reveal"];
export type ClueOut = G["ClueOut"];
export type ClueMeta = G["ClueMeta"];
export type ClueKind = ClueOut["kind"];
export type MoveResult = G["MoveResult"];
export type ModeOut = G["ModeOut"];
export type LevelOut = G["LevelOut"];
export type SessionResult = G["SessionResult"];
export type DailyStatus = G["DailyStatus"];
export type Answer = G["GuessRequest"]["answer"];
export type Domain = ModeOut["domain"];
export type FinalClue = G["FinalClue"];
export type ShownItem = G["ShownItem"];
export type OptionOut = G["OptionOut"];
export type RoundProgress = G["RoundProgress"];
export type MoveStats = G["MoveStats"];
export type SubjectOut = G["SubjectOut"];
export type ModeStart = G["ModeStart"];
/** Формат ответа режима — ключ реестра ответов фронта (D12). */
export type AnswerFormat = NonNullable<ModeOut["answer_format"]>;
/** Подача итога (`Scorer.presentation`) — ключ реестра презентеров итога (D12). */
export type Presentation = NonNullable<ModeOut["presentation"]>;

export type HomePage = B["HomePage"];
export type SoloPage = B["SoloPage"];
export type ResultsPage = B["ResultsPage"];
export type ResultSummary = B["ResultSummary"];
export type ResultRoundCard = B["ResultRoundCard"];
export type ItemCard = B["ItemCard"];
export type DailyCard = B["DailyCard"];
export type ModeCard = B["ModeCard"];
export type SessionInfo = B["SessionInfo"];
export type ModeGroup = B["ModeGroup"];
export type RecordCard = B["RecordCard"];
export type ResultRecord = B["ResultRecord"];
export type BracketSummary = B["BracketSummary"];

export type Suggestion = Catalog["schemas"]["SuggestionOut"];
export type LoginRequest = Identity["schemas"]["LoginRequest"];
export type RegisterRequest = Identity["schemas"]["RegisterRequest"];

// ------------------------------------------------------------------ этап 3b: сборники и медиатека
type C = Collections["schemas"];
type L = Library["schemas"];

export type CollectionsPage = B["CollectionsPage"];
export type CollectionCardOut = B["CollectionCard"];
export type CollectionPageOut = B["CollectionPage"];
export type CollectionDetailOut = B["CollectionDetail"];
export type CollectionItemCard = B["CollectionItemCard"];
export type CollectionModeCard = B["CollectionModeCard"];
export type CollectionMe = B["CollectionMe"];
export type RoundOption = CollectionModeCard["round_options"][number];
export type LibraryPage = B["LibraryPage"];
export type LibraryShelfPage = B["LibraryShelfPage"];
export type ShelfCard = B["ShelfCard"];
export type ShelfItemCard = B["ShelfItemCard"];

export type CollectionsMeta = C["CollectionsMeta"];
export type MetaLimits = C["MetaLimits"];
export type CoverPreset = C["CoverPresetOut"];
export type TagOut = C["TagOut"];
export type DraftOut = C["DraftOut"];
export type DraftBody = C["DraftBody"];
export type DraftItem = C["DraftItem"];
export type CollectionCatalogPage = C["CollectionPage"];
export type CollectionCard = C["CollectionCard"];
export type CollectionDetail = C["CollectionDetail"];
export type MyCollection = C["MyCollectionOut"];
export type MyCollectionsPage = C["MyCollectionsPage"];
export type LikeOut = C["LikeOut"];
export type FavoriteOut = C["FavoriteOut"];
export type CatalogQuery = NonNullable<
  CollectionsOps["catalog_v1_collections_get"]["parameters"]["query"]
>;
export type CollectionSort = NonNullable<CatalogQuery["sort"]>;

export type ShelfOut = L["ShelfOut"];
export type SaveResult = L["SaveResult"];
export type ItemShelves = L["ItemShelvesOut"];
export type SaveSource = L["SaveItemRequest"]["source"];
export type CatalogItem = Catalog["schemas"]["ItemCard"];

// ------------------------------------------------------------------ этап 4a: онлайн (match)
type M = Match["schemas"];

export type RoomView = M["RoomView"];
export type RoomState = M["RoomState"];
export type RoomPhase = RoomView["phase"];
export type RoomSettings = M["RoomSettings"];
export type SettingsOptions = M["SettingsOptions"];
export type SettingsOverride = M["SettingsOverride"];
export type PlayerSlot = M["PlayerSlot"];
export type MatchView = M["MatchView"];
export type MatchPlayerView = M["MatchPlayerView"];
export type PlayerStatus = NonNullable<MatchPlayerView["status"]>;
export type MyRoundView = M["MyRoundView"];
export type LevelClue = M["LevelClue"];
export type RoundRevealView = M["RoundReveal"];
export type GuessResult = M["GuessResult"];
export type MatchAnswer = M["GuessRequest"]["answer"];
export type RoomCommandResult = M["RoomCommandResult"];
export type MatchCommandResult = M["MatchCommandResult"];
export type CommandAccepted = M["CommandAccepted"];
export type RematchResult = M["RematchResult"];
export type TickResult = M["TickResult"];
export type RealtimeSubscription = M["RealtimeSubscription"];
export type ChatOptions = M["ChatOptions"];
export type MatchModeRef = M["ModeRef"];
export type OpenRoomCard = M["OpenRoomCard"];
export type MyMatchCard = M["MyMatchCard"];
export type MyMatchesPage = M["MyMatchesPage"];
export type MatchResult = M["MatchResult"];
export type MatchResultPlayer = M["ResultPlayer"];
export type MatchResultRound = M["ResultRound"];
export type RateKind = M["RateRequest"]["kind"];
export type RealtimeToken = Identity["schemas"]["RealtimeTokenResponse"];

// Этап 4b: очередь рейтинговой дуэли (match) и рейтинг (bff, rating).
export type QueueTicket = M["QueueTicket"];
export type QueueFormat = QueueTicket["format"];
export type OnlineQueue = B["QueuePoolCard"];
export type TierCard = B["TierCard"];
export type RatingCard = B["RatingCard"];
export type SeasonCard = B["SeasonCard"];
export type WeeklyTop = B["WeeklyTopCard"];
export type WeeklyEntry = B["WeeklyEntryCard"];
export type LeaderboardsPage = B["LeaderboardsPage"];
export type LeaderboardCard = B["LeaderboardCard"];
export type LeaderboardEntry = B["LeaderboardEntryCard"];
export type LadderCard = B["LadderCard"];
export type { MatchChange, MatchChanges } from "./rating-types";

// Этап 5a: прогрессия (bff — профиль и свёртка главной; спин колеса — прямо в progression).
export type ProfilePage = B["ProfilePage"];
export type PlayerPage = B["PlayerPage"];
export type PublicProgressCard = B["PublicProgressCard"];
export type ProgressCard = B["ProgressCard"];
export type StreakCard = B["StreakCard"];
export type WalletCard = B["WalletCard"];
export type QuestCard = B["QuestCard"];
export type QuestsCard = B["QuestsCard"];
export type BadgeCard = B["BadgeCard"];
export type WheelCard = B["WheelCard"];
export type WheelSectorCard = B["WheelSectorCard"];
export type SpinCard = B["SpinCard"];
export type PrizeCard = B["PrizeCard"];
export type HomeProgressCard = B["HomeProgressCard"];
export type { SpinResult } from "./progression-types";

// Этап 5b: магазин, оформление, своя аватарка (bff читает, мутации — прямо в progression),
// shell (тема, колокольчик) и уведомления.
export type ShellPage = B["ShellPage"];
export type ShopPage = B["ShopPage"];
export type ShopCatalogCard = B["ShopCatalogCard"];
export type ShopEntryCard = B["ShopEntryCard"];
export type ShopItemState = ShopEntryCard["state"];
export type CosmeticCard = B["CosmeticCard"];
export type AppearanceCard = B["AppearanceCard"];
export type AppearanceSlotCard = B["AppearanceSlotCard"];
export type InventoryCard = B["InventoryCard"];
export type InventoryEntryCard = B["InventoryEntryCard"];
export type AvatarCard = B["AvatarCard"];
export type AvatarRequestCard = B["AvatarRequestCard"];
export type PlayerAppearanceCard = B["PlayerAppearanceCard"];
export type { PurchaseResult, PromoRedeemResult, PlayersAppearance } from "./progression-types";
export type {
  NotificationItem,
  NotificationPage,
  NotificationSettings,
  TypeSetting,
  EmailSubscription,
} from "./notifications-types";
