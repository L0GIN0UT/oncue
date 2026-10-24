import { describe, expect, it } from "vitest";
import { ApiError, NetworkError } from "@/shared/api/errors";
import { socialErrorKey } from "./errors";

const err = (status: number, code: string) => new ApiError(status, { code, title: code, status });

describe("socialErrorKey", () => {
  it("код social.* → ключ без префикса сервиса", () => {
    expect(socialErrorKey(err(403, "social.invite_forbidden"))).toBe(
      "social.error.invite_forbidden",
    );
    expect(socialErrorKey(err(422, "social.comment_rejected"))).toBe(
      "social.error.comment_rejected",
    );
  });

  it("429 любого сервиса — общий текст про лимит, кроме именованных лимитов", () => {
    expect(socialErrorKey(err(429, "rate_limited"))).toBe("social.error.rate_limited");
    expect(socialErrorKey(err(429, "social.comments_rate_limit"))).toBe(
      "social.error.comments_rate_limit",
    );
  });

  it("санкции и бан: online_banned / account_banned имеют свои тексты", () => {
    expect(socialErrorKey(err(403, "match.online_banned"))).toBe("social.error.online_banned");
    expect(socialErrorKey(err(403, "auth.account_banned"))).toBe("social.error.account_banned");
  });

  it("сеть, гость и неизвестный код", () => {
    expect(socialErrorKey(new NetworkError())).toBe("social.error.network");
    expect(socialErrorKey(err(403, "auth.guest_forbidden"))).toBe("social.error.guest_forbidden");
    expect(socialErrorKey(err(500, "boom"))).toBe("social.error.generic");
    expect(socialErrorKey(new Error("x"))).toBe("social.error.generic");
  });
});
