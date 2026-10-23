/** Мигание заголовка вкладки: сигнал «соперник найден», пока вкладка в фоне. */

/**
 * Начать мигание; возвращает `stop` (возвращает исходный заголовок, повтор безопасен).
 * При `prefers-reduced-motion: reduce` заголовок не мигает: просто показывает текст сигнала.
 */
export function flashTitle(text: string, intervalMs = 1000): () => void {
  const original = document.title;
  let shown = true;
  document.title = text;
  const reduce = globalThis.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;
  const timer = reduce
    ? null
    : setInterval(() => {
        shown = !shown;
        document.title = shown ? text : original;
      }, intervalMs);
  let stopped = false;
  return () => {
    if (stopped) return;
    stopped = true;
    if (timer) clearInterval(timer);
    document.title = original;
  };
}

/** Короткий двойной сигнал без аудиофайлов; в окружении без Web Audio молча ничего не делает. */
export function playFoundSignal(): void {
  try {
    const Ctx: typeof AudioContext | undefined =
      globalThis.AudioContext ??
      (globalThis as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
    if (!Ctx) return;
    const ctx = new Ctx();
    const gain = ctx.createGain();
    gain.gain.value = 0.08;
    gain.connect(ctx.destination);
    [660, 880].forEach((freq, i) => {
      const osc = ctx.createOscillator();
      osc.frequency.value = freq;
      osc.connect(gain);
      osc.start(ctx.currentTime + i * 0.18);
      osc.stop(ctx.currentTime + i * 0.18 + 0.14);
    });
    setTimeout(() => void ctx.close(), 800);
  } catch {
    // Автовоспроизведение может быть запрещено браузером — сигнал необязателен.
  }
}
