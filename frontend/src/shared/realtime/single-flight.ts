/**
 * Single-flight по ключу: параллельные вызовы с одним ключом получают одно обещание (как
 * refresh сессии в `shared/api/refresh.ts`). Нужен токенам realtime: Centrifugo просит
 * токен при каждом переподключении, а вкладка не должна заваливать identity и match.
 */
export function singleFlight<A extends unknown[], T>(
  keyOf: (...args: A) => string,
  run: (...args: A) => Promise<T>,
): (...args: A) => Promise<T> {
  const inflight = new Map<string, Promise<T>>();
  return (...args: A) => {
    const key = keyOf(...args);
    const current = inflight.get(key);
    if (current) return current;
    const promise = run(...args).finally(() => inflight.delete(key));
    inflight.set(key, promise);
    return promise;
  };
}
