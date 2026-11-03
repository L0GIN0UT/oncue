/** Длина звучащей части клипа, с: дальше в файле технический паддинг (30 с зациклены до 50 с). */
export const MAX_CLIP_S = 30;

/** Граница воспроизведения уровня, с: длина подачи уровня, но не больше {@link MAX_CLIP_S}. */
export function playbackLimit(seconds: number | null | undefined, duration: number): number {
  const finite = Number.isFinite(duration) && duration > 0 ? duration : MAX_CLIP_S;
  const cap = Math.min(MAX_CLIP_S, finite);
  return seconds && seconds > 0 ? Math.min(seconds, cap) : cap;
}
