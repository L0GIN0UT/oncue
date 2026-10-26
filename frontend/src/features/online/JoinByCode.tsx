"use client";
/**
 * Вход по коду: 8 символов `[A-HJ-NP-Z2-9]`, ввод приводится к верхнему регистру; ссылку на
 * комнату целиком тоже можно вставить. Проверка комнаты и вход — на экране комнаты. Строка
 * «код + Войти» из макетов «Онлайн» и «Лобби»; кнопка вторичная — главное действие экрана другое.
 */
import { useId, useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { Button } from "@/shared/ui/button";
import { isRoomCode, normalizeRoomCode, ROOM_CODE_LENGTH } from "@/features/match/code";

export function JoinByCode() {
  const t = useTranslations();
  const router = useRouter();
  const id = useId();
  const [code, setCode] = useState("");
  const [error, setError] = useState(false);

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    const normalized = normalizeRoomCode(code);
    if (!isRoomCode(normalized)) {
      setError(true);
      return;
    }
    router.push(`/room/${normalized}`);
  };

  return (
    <form
      onSubmit={onSubmit}
      noValidate
      className="flex flex-col gap-1.5"
      data-testid="join-by-code"
    >
      <label htmlFor={`${id}-code`} className="sr-only">
        {t("online.join.title")}
      </label>
      <div className="flex h-12 items-center gap-2 rounded-xl bg-stage-raised pr-2 pl-3">
        <input
          id={`${id}-code`}
          name="code"
          value={code}
          onChange={(e) => {
            setCode(normalizeRoomCode(e.target.value).slice(0, ROOM_CODE_LENGTH));
            setError(false);
          }}
          autoComplete="off"
          autoCapitalize="characters"
          spellCheck={false}
          inputMode="text"
          placeholder="K7MQ2XWP"
          aria-describedby={`${id}-hint${error ? ` ${id}-error` : ""}`}
          aria-invalid={error}
          className="tabular min-w-0 flex-1 bg-transparent font-display text-sm font-extrabold tracking-[0.08em] text-ink-soft uppercase outline-none placeholder:text-ink-faint"
          data-testid="room-code-input"
        />
        <Button type="submit" size="sm" variant="secondary" data-testid="room-code-submit">
          {t("online.join.submit")}
        </Button>
      </div>
      <p id={`${id}-hint`} className="text-xs text-ink-muted">
        {t("online.join.hint")}
      </p>
      {error && (
        <p
          id={`${id}-error`}
          role="alert"
          className="text-sm text-miss"
          data-testid="room-code-error"
        >
          {t("online.join.invalid")}
        </p>
      )}
    </form>
  );
}
