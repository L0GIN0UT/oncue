"use client";
/**
 * «Играть с друзьями» на странице сборника: кастомное лобби по онлайн-шаблону домена с
 * источником `collection` (`GET /v1/modes?session=online`, `online.kind = lobby`) и
 * `collection_ids = [сборник]`. Шаблона у домена нет — кнопки нет. Онлайн — только с
 * аккаунтом (D13): гостю и анониму — ссылка на вход с возвратом на сборник.
 */
import Link from "next/link";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { Users } from "lucide-react";
import { api, unwrap } from "@/shared/api/client";
import type { Domain, ModeOut } from "@/shared/api/types";
import { Button } from "@/shared/ui/button";
import { useDynamicT } from "@/shared/i18n/dynamic";
import { useViewer } from "@/features/auth/session";
import { withNext } from "@/features/auth/AuthForms";
import { createRoom } from "./api";
import { matchErrorKey } from "./errors";

/** Онлайн-шаблон лобби по сборнику для домена (по данным game, без ключей режимов). */
export function collectionLobbyTemplate(modes: ModeOut[], domain: Domain): ModeOut | null {
  return (
    modes.find(
      (m) =>
        m.domain === domain &&
        m.source_type === "collection" &&
        m.online?.kind === "lobby" &&
        !m.online.ranked,
    ) ?? null
  );
}

export function PlayWithFriends({
  collectionId,
  domain,
}: {
  collectionId: string;
  domain: Domain;
}) {
  const t = useTranslations();
  const dt = useDynamicT();
  const router = useRouter();
  const viewer = useViewer();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const modes = useQuery({
    queryKey: ["game", "modes", "online", domain],
    queryFn: async () =>
      unwrap(
        await api().game.GET("/v1/modes", { params: { query: { session: "online", domain } } }),
      ),
    staleTime: 60_000,
  });
  const template = modes.data ? collectionLobbyTemplate(modes.data, domain) : null;
  if (!template) return null;

  const start = async () => {
    setBusy(true);
    setError(null);
    try {
      const room = await createRoom({ mode_key: template.key, collection_ids: [collectionId] });
      router.push(`/room/${room.code}`);
    } catch (e) {
      setError(dt(matchErrorKey(e), t("match.error.generic")));
      setBusy(false);
    }
  };

  return (
    <div className="flex flex-col items-start gap-2" data-testid="play-with-friends">
      {viewer.kind === "user" ? (
        <Button
          variant="secondary"
          size="lg"
          disabled={busy}
          onClick={start}
          data-testid="play-with-friends-start"
        >
          <Users aria-hidden />
          {t("match.friends.action")}
        </Button>
      ) : (
        <Button asChild variant="secondary" size="lg">
          <Link
            href={withNext("/login", `/collections/${collectionId}`)}
            data-testid="play-with-friends-login"
          >
            <Users aria-hidden />
            {t("match.friends.login")}
          </Link>
        </Button>
      )}
      <p className="text-sm text-ink-muted">{t("match.friends.hint")}</p>
      {error && (
        <p role="alert" className="text-sm text-miss">
          {error}
        </p>
      )}
    </div>
  );
}
