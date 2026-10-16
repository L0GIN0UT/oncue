/** Сегмент адреса без процент-кодирования; битая последовательность (`%E0%A4%A`) — как есть. */
export function safeDecode(segment: string): string {
  try {
    return decodeURIComponent(segment);
  } catch {
    return segment;
  }
}
