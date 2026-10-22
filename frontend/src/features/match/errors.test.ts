import { describe, expect, it } from "vitest";
import { ApiError, NetworkError } from "@/shared/api/errors";
import { matchErrorKey } from "./errors";

describe("matchErrorKey", () => {
  it("код match.* → ключ match.error.*", () => {
    expect(matchErrorKey(new ApiError(409, { code: "match.room_full" }))).toBe(
      "match.error.room_full",
    );
  });

  it("отказ чата — по причине details.reason; неизвестная причина — общий текст отказа", () => {
    const rejected = (reason: unknown) =>
      matchErrorKey(new ApiError(422, { code: "match.chat_rejected", details: { reason } }));
    expect(rejected("denylist")).toBe("match.error.chat_rejected_denylist");
    expect(rejected("link")).toBe("match.error.chat_rejected_link");
    expect(rejected("__proto__")).toBe("match.error.chat_rejected");
  });

  it("429, гость, сеть и чужие коды", () => {
    expect(matchErrorKey(new ApiError(429, { code: "common.rate_limited" }))).toBe(
      "match.error.rate_limited",
    );
    expect(matchErrorKey(new ApiError(403, { code: "auth.guest_forbidden" }))).toBe(
      "match.error.guest_forbidden",
    );
    expect(matchErrorKey(new NetworkError())).toBe("match.error.network");
    expect(matchErrorKey(new ApiError(500, null))).toBe("match.error.generic");
  });
});
