"use client";
/**
 * Жалоба (этап 5c): кнопка и диалог. Причины — справочник social (`GET /v1/report-reasons`),
 * отфильтрованный по виду объекта; текст до 300 символов; отправка `POST /v1/reports` с ключом
 * идемпотентности. Повтор на тот же объект сервер отвечает `repeated = true` — «уже
 * отправлена». Состояние кейса заявителю не раскрывается.
 */
import { useEffect, useId, useRef, useState, type FormEvent } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { Flag } from "lucide-react";
import type { ReportKind } from "@/shared/api/types";
import { Button } from "@/shared/ui/button";
import { ErrorLine } from "@/features/friends/ErrorLine";
import { REPORT_TEXT_MAX_LENGTH } from "@/features/friends/config";
import { fetchReportReasons, fileReport, friendsKeys } from "@/features/friends/api";
import { useSocialAction } from "@/features/friends/use-action";

type Props = {
  kind: ReportKind;
  /** player — user_id; collection, comment — их id; avatar — id ассета. */
  targetId: string;
  /** Обязателен для жалобы на игрока: общий матч. */
  contextMatchId?: string | null;
  /** Подпись кнопки (по умолчанию «Пожаловаться»). */
  label?: string;
  /** Подробная подпись для скринридера: на кого жалоба. */
  ariaLabel?: string;
  size?: "sm" | "md";
  testId?: string;
};

export function ReportButton({
  kind,
  targetId,
  contextMatchId = null,
  label,
  ariaLabel,
  size = "sm",
  testId = "report-open",
}: Props) {
  const t = useTranslations();
  const [open, setOpen] = useState(false);
  const [done, setDone] = useState<"sent" | "repeated" | null>(null);
  const trigger = useRef<HTMLButtonElement>(null);

  if (done)
    return (
      <span className="text-sm text-ink-muted" role="status" data-testid="report-done">
        {t(done === "sent" ? "social.report.sent" : "social.report.repeated")}
      </span>
    );

  return (
    <>
      <Button
        ref={trigger}
        variant="ghost"
        size={size}
        onClick={() => setOpen(true)}
        aria-label={ariaLabel}
        data-testid={testId}
      >
        <Flag aria-hidden />
        {label ?? t("social.report.open")}
      </Button>
      {open && (
        <ReportDialog
          kind={kind}
          targetId={targetId}
          contextMatchId={contextMatchId}
          onClose={() => {
            setOpen(false);
            trigger.current?.focus();
          }}
          onDone={(repeated) => {
            setOpen(false);
            setDone(repeated ? "repeated" : "sent");
          }}
        />
      )}
    </>
  );
}

function ReportDialog({
  kind,
  targetId,
  contextMatchId,
  onClose,
  onDone,
}: {
  kind: ReportKind;
  targetId: string;
  contextMatchId: string | null;
  onClose: () => void;
  onDone: (repeated: boolean) => void;
}) {
  const t = useTranslations();
  const titleId = useId();
  const action = useSocialAction();
  const [reason, setReason] = useState<string | null>(null);
  const [text, setText] = useState("");
  const panel = useRef<HTMLDivElement>(null);
  const reasons = useQuery({
    queryKey: friendsKeys.reasons,
    queryFn: fetchReportReasons,
    staleTime: 5 * 60_000,
  });

  // Фокус уходит в диалог один раз при открытии; закрытие по Escape берёт актуальный обработчик.
  const close = useRef(onClose);
  useEffect(() => {
    close.current = onClose;
  });
  useEffect(() => {
    panel.current?.querySelector<HTMLElement>("input, textarea, button")?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") close.current();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  const options = (reasons.data ?? []).filter((r) => r.target_kinds.includes(kind));

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!reason) return;
    const body = {
      target_kind: kind,
      target_id: targetId,
      reason_code: reason,
      text: text.trim() || null,
      context_match_id: contextMatchId,
    };
    const r = await action.run(`report:${kind}:${targetId}`, (key) => fileReport(body, key));
    if (r.ok) onDone(r.value.repeated);
  };

  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-night/80 p-4">
      <div
        ref={panel}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        data-testid="report-dialog"
        className="flex max-h-full w-full max-w-md flex-col gap-4 overflow-y-auto rounded-[var(--radius-ticket)] bg-stage p-6 border-[1.5px] border-rule"
      >
        <h2 id={titleId} className="font-semibold">
          {t(`social.report.title.${kind}`)}
        </h2>
        <form onSubmit={(e) => void submit(e)} className="flex flex-col gap-4">
          {reasons.isPending ? (
            <p className="text-ink-muted" role="status">
              {t("common.loading")}
            </p>
          ) : reasons.isError || options.length === 0 ? (
            <p role="alert" className="text-miss">
              {t("social.report.reasonsError")}
            </p>
          ) : (
            <fieldset className="flex flex-col gap-2">
              <legend className="mb-1 text-sm font-medium">{t("social.report.reason")}</legend>
              {options.map((r) => (
                <label key={r.code} className="flex items-center gap-3">
                  <input
                    type="radio"
                    name="report-reason"
                    value={r.code}
                    className="size-5 accent-[var(--color-music)]"
                    checked={reason === r.code}
                    onChange={() => setReason(r.code)}
                    data-testid={`report-reason-${r.code}`}
                  />
                  <span dir="auto">{r.title}</span>
                </label>
              ))}
            </fieldset>
          )}
          <label className="flex flex-col gap-1.5 text-sm font-medium">
            {t("social.report.text")}
            <textarea
              value={text}
              maxLength={REPORT_TEXT_MAX_LENGTH}
              onChange={(e) => setText(e.target.value)}
              rows={3}
              data-testid="report-text"
              className="rounded-[var(--radius-control)] bg-stage p-3 font-normal text-ink border-[1.5px] border-rule outline-none focus:border-music focus:shadow-[0_0_0_4px_var(--color-glow)]"
            />
            <span className="tabular self-end text-xs font-normal text-ink-faint">
              {text.length}/{REPORT_TEXT_MAX_LENGTH}
            </span>
          </label>
          <ErrorLine text={action.error} />
          <div className="flex flex-wrap justify-end gap-2">
            <Button type="button" variant="ghost" onClick={onClose}>
              {t("social.cancel")}
            </Button>
            <Button type="submit" disabled={!reason || action.busy} data-testid="report-submit">
              {t("social.report.submit")}
            </Button>
          </div>
        </form>
      </div>
    </div>
  );
}
