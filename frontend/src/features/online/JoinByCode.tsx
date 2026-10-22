"use client";
/**
 * Вход по коду: 8 символов `[A-HJ-NP-Z2-9]`, ввод приводится к верхнему регистру; ссылку на
 * комнату целиком тоже можно вставить. Проверка комнаты и вход — на экране комнаты.
 */
import { useId, useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { LogIn } from "lucide-react";
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
      className="flex flex-col gap-3 rounded-[var(--radius-ticket)] bg-stage p-5 ring-1 ring-rule sm:p-6"
      data-testid="join-by-code"
    >
      <label htmlFor={`${id}-code`} className="font-display text-lg font-semibold">
        {t("online.join.title")}
      </label>
      <p id={`${id}-hint`} className="text-sm text-ink-muted">
        {t("online.join.hint")}
      </p>
      <div className="flex flex-col gap-3 sm:flex-row">
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
          className="tabular h-12 min-w-0 flex-1 rounded-[var(--radius-control)] bg-night px-4 font-display text-xl tracking-[0.3em] uppercase ring-1 ring-rule ring-inset placeholder:text-ink-faint/60 focus-visible:ring-2 focus-visible:ring-[var(--color-music)] focus-visible:outline-none"
          data-testid="room-code-input"
        />
        <Button type="submit" size="lg" className="h-12" data-testid="room-code-submit">
          <LogIn aria-hidden />
          {t("online.join.submit")}
        </Button>
      </div>
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
