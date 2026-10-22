"use client";
/** `/match/[id]`: итог сыгранного матча из bff `/v1/pages/match/{id}` (D3 плана этапа 4). */
import Link from "next/link";
import { useTranslations } from "next-intl";
import { Button } from "@/shared/ui/button";
import { OnlineGate } from "@/features/online/OnlineGate";
import { useMyUserId } from "./api";
import { MatchSummaryView } from "./MatchSummaryView";

export function MatchResultView({ matchId }: { matchId: string }) {
  const t = useTranslations();
  return (
    <OnlineGate>
      <div className="flex flex-col gap-8 pt-6 sm:pt-10">
        <MatchResultBody matchId={matchId} />
        <div className="flex flex-wrap gap-3">
          <Button asChild variant="secondary">
            <Link href="/me">{t("online.myMatches.title")}</Link>
          </Button>
          <Button asChild variant="ghost">
            <Link href="/online">{t("match.toOnline")}</Link>
          </Button>
        </div>
      </div>
    </OnlineGate>
  );
}

function MatchResultBody({ matchId }: { matchId: string }) {
  const myId = useMyUserId();
  return <MatchSummaryView matchId={matchId} myId={myId} />;
}
