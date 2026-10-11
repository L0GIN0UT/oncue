/** Ошибки API в формате RFC 9457 (Problem Details) и сетевые сбои. */
import type { Problem } from "./types";

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: Record<string, unknown> | null;

  constructor(status: number, problem: Partial<Problem> | null) {
    super(problem?.title ?? `HTTP ${status}`);
    this.name = "ApiError";
    this.status = status;
    this.code = problem?.code ?? `http.${status}`;
    this.details = problem?.details ?? null;
  }
}

/** Запрос не дошёл до сервера или ответ не получен (обрыв, таймаут). */
export class NetworkError extends Error {
  constructor(cause?: unknown) {
    super("network error", { cause });
    this.name = "NetworkError";
  }
}

export function isApiError(e: unknown, code?: string): e is ApiError {
  return e instanceof ApiError && (code === undefined || e.code === code);
}

/** Разобрать тело ответа-ошибки; чужой формат превращается в код `http.<status>`. */
export async function toApiError(res: Response): Promise<ApiError> {
  let problem: Partial<Problem> | null = null;
  try {
    const body: unknown = await res.json();
    if (body && typeof body === "object" && "code" in body) problem = body as Partial<Problem>;
  } catch {
    problem = null;
  }
  return new ApiError(res.status, problem);
}
