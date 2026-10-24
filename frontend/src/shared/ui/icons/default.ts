/** Набор иконок темы `default` (lucide). Новые темы (5d) дают свой набор с теми же именами. */
import {
  Bell,
  Check,
  CircleAlert,
  Coins,
  Lock,
  Sparkles,
  Shirt,
  ShoppingBag,
  Upload,
  UserRound,
  type LucideIcon,
} from "lucide-react";

export const defaultIcons = {
  bell: Bell,
  shop: ShoppingBag,
  verses: Coins,
  locked: Lock,
  owned: Check,
  upload: Upload,
  avatar: UserRound,
  rarity: Sparkles,
  appearance: Shirt,
  warning: CircleAlert,
} as const satisfies Record<string, LucideIcon>;

export type IconName = keyof typeof defaultIcons;
export type IconSet = Record<IconName, LucideIcon>;
