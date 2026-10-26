"use client";
/**
 * Добавление в друзья: по нику или по коду (ровно одно поле уходит в social) и «мой код» с
 * перевыпуском. Ответ на неизвестный ник и закрытые заявки одинаков (404) — экран не
 * подсказывает, какой из случаев произошёл.
 */
import { useState, type FormEvent } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { Button } from "@/shared/ui/button";
import { Field } from "@/shared/ui/field";
import { ErrorLine } from "./ErrorLine";
import { friendsKeys, fetchMyCode, regenerateCode, sendRequest } from "./api";
import { useSocialAction } from "./use-action";

export function AddFriendPanel() {
  const t = useTranslations();
  const client = useQueryClient();
  const action = useSocialAction();
  const [nickname, setNickname] = useState("");
  const [code, setCode] = useState("");
  const [sent, setSent] = useState<string | null>(null);

  const submit = async (e: FormEvent, by: "nickname" | "code") => {
    e.preventDefault();
    const value = (by === "nickname" ? nickname : code).trim();
    if (!value) return;
    setSent(null);
    const target = by === "nickname" ? { nickname: value } : { code: value.toUpperCase() };
    const r = await action.run(`send:${by}:${value}`, (key) => sendRequest(target, key));
    if (!r.ok) return;
    setSent(r.value.nickname);
    if (by === "nickname") setNickname("");
    else setCode("");
    await client.invalidateQueries({ queryKey: friendsKeys.requests });
    await client.invalidateQueries({ queryKey: friendsKeys.list });
  };

  return (
    <div className="flex flex-col gap-8">
      <section aria-labelledby="add-nick" className="flex max-w-md flex-col gap-3">
        <h2 id="add-nick" className="font-semibold">
          {t("social.add.byNickname")}
        </h2>
        <form onSubmit={(e) => void submit(e, "nickname")} className="flex flex-col gap-3">
          <Field
            label={t("social.add.nickname")}
            value={nickname}
            onChange={(e) => setNickname(e.target.value)}
            autoComplete="off"
            maxLength={64}
            data-testid="add-nickname"
          />
          <Button
            type="submit"
            disabled={action.busy || !nickname.trim()}
            data-testid="add-nickname-submit"
          >
            {t("social.add.send")}
          </Button>
        </form>
      </section>
      <section aria-labelledby="add-code" className="flex max-w-md flex-col gap-3">
        <h2 id="add-code" className="font-semibold">
          {t("social.add.byCode")}
        </h2>
        <form onSubmit={(e) => void submit(e, "code")} className="flex flex-col gap-3">
          <Field
            label={t("social.add.code")}
            value={code}
            onChange={(e) => setCode(e.target.value)}
            autoComplete="off"
            maxLength={16}
            hint={t("social.add.codeHint")}
            data-testid="add-code"
          />
          <Button
            type="submit"
            disabled={action.busy || !code.trim()}
            data-testid="add-code-submit"
          >
            {t("social.add.send")}
          </Button>
        </form>
      </section>
      {sent && (
        <p role="status" className="text-exact" data-testid="add-sent">
          {t("social.add.sent", { name: sent })}
        </p>
      )}
      <ErrorLine text={action.error} />
      <MyCode />
    </div>
  );
}

function MyCode() {
  const t = useTranslations();
  const client = useQueryClient();
  const action = useSocialAction();
  const query = useQuery({ queryKey: friendsKeys.code, queryFn: fetchMyCode });

  const regenerate = async () => {
    const r = await action.run("regenerate", (key) => regenerateCode(key));
    if (r.ok) client.setQueryData(friendsKeys.code, r.value);
  };

  return (
    <section aria-labelledby="my-code" className="flex max-w-md flex-col gap-3">
      <h2 id="my-code" className="font-semibold">
        {t("social.code.title")}
      </h2>
      <p className="text-sm text-ink-muted">{t("social.code.lead")}</p>
      {query.isPending ? (
        <p className="text-ink-muted" role="status">
          {t("common.loading")}
        </p>
      ) : query.isError ? (
        <p role="alert" className="text-miss">
          {t("social.loadError")}
        </p>
      ) : (
        <p
          className="tabular w-fit rounded-[var(--radius-control)] border-[1.5px] border-rule bg-stage px-4 py-2 font-display text-2xl tracking-widest"
          data-testid="my-code"
        >
          {query.data.code}
        </p>
      )}
      <Button
        variant="secondary"
        className="w-fit"
        disabled={action.busy}
        onClick={() => void regenerate()}
        data-testid="code-regenerate"
      >
        {t("social.code.regenerate")}
      </Button>
      <ErrorLine text={action.error} />
    </section>
  );
}
