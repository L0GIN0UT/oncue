"use client";
/**
 * Вход по коду: 8 символов `[A-HJ-NP-Z2-9]`, ввод приводится к верхнему регистру; ссылку на
 * комнату целиком тоже можно вставить. Проверка комнаты и вход — на экране комнаты.
 * Кнопка вторичная: на хабе главное действие — поиск соперника; `primary` — для экрана `/lobbies`.
 */
import { useId, useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { Button } from "@/shared/ui/button";
import { inputClass } from "@/shared/ui/field";
import { cn } from "@/shared/lib/cn";
import { isRoomCode, normalizeRoomCode, ROOM_CODE_LENGTH } from "@/features/match/code";

export function JoinByCode({ primary = false }: { primary?: boolean }) {
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
    <form onSubmit={onSubmit} noValidate className="flex flex-col gap-3" data-testid="join-by-code">
      <label htmlFor={`${id}-code`} className="text-sm font-medium">
        {t("online.join.title")}
      </label>
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
          className={cn(
            inputClass,
            "tabular h-14 min-w-0 flex-1 font-display text-xl tracking-[0.3em] uppercase",
          )}
          data-testid="room-code-input"
        />
        <Button
          type="submit"
          size="lg"
          variant={primary ? "primary" : "secondary"}
          data-testid="room-code-submit"
        >
          {t("online.join.submit")}
        </Button>
      </div>
      <p id={`${id}-hint`} className="text-[13px] text-ink-muted">
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
