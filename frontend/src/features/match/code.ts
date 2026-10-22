/**
 * Код комнаты (D12 плана этапа 4): 8 символов `[A-HJ-NP-Z2-9]` — без 0/O/1/I, чтобы код
 * диктовали голосом без ошибок. Ввод нормализуется: регистр, пробелы и дефисы, ссылка
 * на комнату целиком (`…/room/CODE`).
 */
export const ROOM_CODE_LENGTH = 8;
export const ROOM_CODE_RE = /^[A-HJ-NP-Z2-9]{8}$/;

/** Привести ввод к виду кода: верхний регистр, без разделителей; из ссылки — код комнаты. */
export function normalizeRoomCode(input: string): string {
  const fromLink = /\/room\/([^/?#\s]+)/i.exec(input)?.[1];
  return (fromLink ?? input).toUpperCase().replace(/[\s\-_.]/g, "");
}

export function isRoomCode(code: string): boolean {
  return ROOM_CODE_RE.test(code);
}
