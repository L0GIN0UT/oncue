"use client";
/**
 * «Своя аватарка» в профиле: статус (куплено ли право, текущая, заявка pending / approved /
 * rejected с причиной), загрузка файла и ссылка в магазин, если права нет. Размер и формат
 * проверяются до отправки по лимитам блока `avatar` (настоящую проверку делает сервер).
 * Загрузка — multipart `POST /v1/me/avatar` прямо в progression с `Idempotency-Key`; ключ
 * привязан к выбранному файлу (повтор после сбоя — тот же, новый файл — новый).
 * До одобрения модератором виден прежний аватар.
 */
import { useEffect, useRef, useState, type ChangeEvent } from "react";
import Link from "next/link";
import { useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import type { AvatarCard } from "@/shared/api/types";
import { Button } from "@/shared/ui/button";
import { CosmeticImage } from "@/features/cosmetics/CosmeticImage";
import { newIdempotencyKey, submitAvatar } from "@/features/shop/api";
import { profileQueryKey } from "./api";
import {
  avatarReasonKey,
  avatarUploadErrorKey,
  formatBytes,
  validateAvatarFile,
  type AvatarUploadError,
  type FileProblem,
} from "./avatar-logic";

type Problem = FileProblem | AvatarUploadError;

const PROBLEM_KEYS = {
  tooBig: "avatar.error.tooBig",
  format: "avatar.error.format",
  invalid: "avatar.error.invalid",
  tooLarge: "avatar.error.tooLarge",
  rateLimited: "avatar.error.rateLimited",
  notEntitled: "avatar.error.notEntitled",
  unavailable: "avatar.error.unavailable",
  network: "avatar.error.network",
  other: "avatar.error.other",
} as const satisfies Record<Problem, string>;

type Status = "none" | "current" | "pending" | "approved" | "rejected";

function statusOf(avatar: AvatarCard, currentUrl: string | null | undefined): Status {
  const s = avatar.request?.status;
  if (s === "pending" || s === "rejected" || s === "approved") return s;
  return currentUrl ? "current" : "none";
}

const STATUS_KEYS = {
  none: "avatar.status.none",
  current: "avatar.status.current",
  pending: "avatar.status.pending",
  approved: "avatar.status.approved",
  rejected: "avatar.status.rejected",
} as const satisfies Record<Status, string>;

export function AvatarBlock({
  avatar,
  currentUrl,
}: {
  avatar: AvatarCard;
  /** Одобренная своя аватарка из блока `appearance`. */
  currentUrl?: string | null;
}) {
  const t = useTranslations();
  const client = useQueryClient();
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const [problem, setProblem] = useState<Problem | null>(null);
  const [busy, setBusy] = useState(false);
  const [sent, setSent] = useState(false);
  const key = useRef<string | null>(null);
  const lock = useRef(false);

  useEffect(
    () => () => {
      if (preview) URL.revokeObjectURL(preview);
    },
    [preview],
  );

  const status = statusOf(avatar, currentUrl);
  const reasonKey = status === "rejected" ? avatarReasonKey(avatar.request?.reason_code) : null;

  const choose = async (e: ChangeEvent<HTMLInputElement>) => {
    const picked = e.target.files?.[0] ?? null;
    setSent(false);
    setFile(null);
    setPreview(null);
    key.current = null;
    if (!picked) return setProblem(null);
    const input = e.target;
    const bad = await validateAvatarFile(picked, avatar);
    setProblem(bad);
    if (bad) {
      input.value = "";
      return;
    }
    setFile(picked);
    setPreview(URL.createObjectURL(picked));
    key.current = newIdempotencyKey();
  };

  const send = async () => {
    if (!file || !key.current || lock.current) return;
    lock.current = true;
    setBusy(true);
    setProblem(null);
    try {
      await submitAvatar(file, key.current);
      setSent(true);
      setFile(null);
      setPreview(null);
      key.current = null;
      await client.invalidateQueries({ queryKey: profileQueryKey });
    } catch (e) {
      setProblem(avatarUploadErrorKey(e));
    } finally {
      lock.current = false;
      setBusy(false);
    }
  };

  return (
    <div className="flex flex-col gap-3">
      <p className="text-sm text-ink-muted">{t("avatar.lead")}</p>
      <div aria-live="polite">
        <p role="status" className="text-sm" data-testid="avatar-status" data-status={status}>
          {t(STATUS_KEYS[status])}
          {status === "rejected" && <> {reasonKey ? t(reasonKey) : t("avatar.rejectedGeneric")}</>}
        </p>
      </div>

      {!avatar.entitled ? (
        <div className="flex flex-col items-start gap-2">
          <p className="text-ink-muted">{t("avatar.notEntitled")}</p>
          <Button asChild variant="secondary" size="sm">
            <Link href="/shop">{t("avatar.toShop")}</Link>
          </Button>
        </div>
      ) : (
        <div className="flex flex-col gap-3">
          <label className="flex flex-col gap-1.5 text-sm font-medium">
            {t("avatar.fileLabel")}
            <input
              type="file"
              accept={avatar.formats.map((f) => `image/${f}`).join(",")}
              onChange={(e) => void choose(e)}
              data-testid="avatar-upload"
              className="text-sm font-normal text-ink-muted file:mr-3 file:rounded-[var(--radius-control)] file:bg-stage-raised file:px-3 file:py-2 file:text-ink"
            />
          </label>
          <p className="text-xs text-ink-faint">
            {t("avatar.limits", {
              formats: avatar.formats.join(", "),
              size: formatBytes(avatar.max_bytes),
            })}
          </p>
          {preview && (
            <CosmeticImage
              item={{
                title: t("avatar.preview"),
                media_kind: "static",
                asset_url: preview,
                poster_url: null,
              }}
              allowBlob
              className="size-24 rounded-full"
            />
          )}
          {file && (
            <Button
              size="sm"
              className="self-start"
              onClick={() => void send()}
              disabled={busy}
              aria-busy={busy}
            >
              {busy ? t("avatar.busy") : t("avatar.submit")}
            </Button>
          )}
          <div aria-live="polite">
            {problem && (
              <p role="alert" className="text-sm text-miss" data-testid="avatar-error">
                {t(PROBLEM_KEYS[problem])}
              </p>
            )}
            {sent && (
              <p className="text-sm text-exact" data-testid="avatar-sent">
                {t("avatar.sent")}
              </p>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
