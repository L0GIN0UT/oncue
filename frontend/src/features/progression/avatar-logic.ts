/** Логика «Своей аватарки»: клиентская проверка файла по лимитам, причины отклонения, ошибки. */
import { ApiError, NetworkError } from "@/shared/api/errors";

export type AvatarLimits = { formats: string[]; max_bytes: number };

export type FileProblem = "tooBig" | "format" | "invalid";

const HEAD_BYTES = 12;

/** Первые байты файла: `Blob.arrayBuffer` есть не везде (jsdom), тогда — FileReader. */
async function readHead(file: File): Promise<Uint8Array> {
  const head = file.slice(0, HEAD_BYTES);
  if (typeof head.arrayBuffer === "function") return new Uint8Array(await head.arrayBuffer());
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(new Uint8Array(reader.result as ArrayBuffer));
    reader.onerror = () => reject(reader.error);
    reader.readAsArrayBuffer(head);
  });
}

const ascii = (bytes: Uint8Array, from: number, text: string) =>
  [...text].every((c, i) => bytes[from + i] === c.charCodeAt(0));

/** Формат по сигнатуре (имя из лимитов): имя файла и `file.type` не авторитетны. */
export function sniffFormat(bytes: Uint8Array): string | null {
  if (bytes[0] === 0xff && bytes[1] === 0xd8 && bytes[2] === 0xff) return "jpeg";
  if (bytes[0] === 0x89 && ascii(bytes, 1, "PNG") && bytes[4] === 0x0d && bytes[5] === 0x0a)
    return "png";
  if (ascii(bytes, 0, "RIFF") && ascii(bytes, 8, "WEBP")) return "webp";
  return null;
}

/**
 * Проверка до отправки: размер и формат по сигнатуре (первые 12 байт). Это удобство, а не
 * защита: настоящую проверку содержимого, размеров и пересжатие делает сервер (media-pipeline).
 */
export async function validateAvatarFile(
  file: File,
  limits: AvatarLimits,
): Promise<FileProblem | null> {
  if (file.size <= 0) return "invalid";
  if (file.size > limits.max_bytes) return "tooBig";
  let format: string | null;
  try {
    format = sniffFormat(await readHead(file));
  } catch {
    return "invalid";
  }
  return format && limits.formats.includes(format) ? null : "format";
}

const REASON_KEYS = {
  inappropriate: "avatar.reason.inappropriate",
  copyright: "avatar.reason.copyright",
  low_quality: "avatar.reason.low_quality",
  personal_data: "avatar.reason.personal_data",
  other: "avatar.reason.other",
} as const;

/** Ключ ru-подписи известного кода причины; неизвестный код — `null` (общий текст). */
export function avatarReasonKey(code: string | null | undefined) {
  return code && Object.hasOwn(REASON_KEYS, code)
    ? REASON_KEYS[code as keyof typeof REASON_KEYS]
    : null;
}

export type AvatarUploadError =
  "tooLarge" | "invalid" | "rateLimited" | "notEntitled" | "unavailable" | "network" | "other";

export function avatarUploadErrorKey(e: unknown): AvatarUploadError {
  if (e instanceof NetworkError) return "network";
  if (!(e instanceof ApiError)) return "other";
  if (e.status === 413) return "tooLarge";
  if (e.status === 422) return "invalid";
  if (e.status === 429) return "rateLimited";
  if (e.status === 403 && e.code === "progression.avatar_not_entitled") return "notEntitled";
  if (e.status === 503) return "unavailable";
  return "other";
}

export function formatBytes(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1).replace(".", ",")} МБ`;
  if (bytes >= 1024) return `${Math.round(bytes / 1024)} КБ`;
  return `${bytes} Б`;
}
