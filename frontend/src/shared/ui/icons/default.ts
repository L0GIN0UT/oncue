/**
 * Набор иконок темы `default` (lucide): штрих 2 px (по умолчанию у lucide), базовый размер 22 px
 * (`ICON_SIZE`). Новые темы (5d) дают свой набор с теми же именами.
 */
import {
  ArrowLeft,
  Bell,
  CalendarDays,
  Check,
  ChevronDown,
  ChevronRight,
  CircleAlert,
  CircleHelp,
  Coins,
  Compass,
  Film,
  Lock,
  LogOut,
  Menu,
  Music,
  Pause,
  Play,
  Plus,
  Search,
  Share2,
  Sparkles,
  Trophy,
  Shirt,
  ShoppingBag,
  Upload,
  UserRound,
  X,
  type LucideIcon,
} from "lucide-react";

/** Размер иконок интерфейса, px. */
export const ICON_SIZE = 22;

export const defaultIcons = {
  bell: Bell,
  leaders: Trophy,
  shop: ShoppingBag,
  verses: Coins,
  locked: Lock,
  owned: Check,
  upload: Upload,
  avatar: UserRound,
  rarity: Sparkles,
  appearance: Shirt,
  warning: CircleAlert,
  event: Compass,
  spot: Search,
  menu: Menu,
  close: X,
  back: ArrowLeft,
  exit: LogOut,
  help: CircleHelp,
  calendar: CalendarDays,
  play: Play,
  pause: Pause,
  correct: Check,
  wrong: X,
  music: Music,
  film: Film,
  plus: Plus,
  share: Share2,
  expand: ChevronDown,
  next: ChevronRight,
} as const satisfies Record<string, LucideIcon>;

export type IconName = keyof typeof defaultIcons;
export type IconSet = Record<IconName, LucideIcon>;
