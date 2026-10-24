import { describe, expect, it } from "vitest";
import { ApiError, NetworkError } from "@/shared/api/errors";
import { equipErrorKey, purchaseErrorKey } from "./errors";

const api = (status: number, code: string, retry: number | null = null) =>
  new ApiError(status, { code, title: code, status } as never, retry);

describe("purchaseErrorKey", () => {
  it.each([
    [api(409, "progression.insufficient_funds"), "insufficientFunds"],
    [api(409, "progression.already_owned"), "alreadyOwned"],
    [api(403, "progression.item_not_available"), "notAvailable"],
    [api(404, "progression.item_not_found"), "notFound"],
    [api(404, "что-то-другое"), "notFound"],
    [api(401, "auth.unauthenticated"), "auth"],
    [api(403, "auth.guest_forbidden"), "auth"],
    [api(429, "rate_limited"), "rateLimited"],
    [api(500, "internal"), "unavailable"],
    [api(503, "x"), "unavailable"],
    [api(400, "x"), "other"],
    [new NetworkError(), "network"],
    [new Error("x"), "other"],
  ])("%#", (error, key) => {
    expect(purchaseErrorKey(error)).toBe(key);
  });
});

describe("equipErrorKey", () => {
  it.each([
    [api(404, "progression.item_not_owned"), "notOwned"],
    [api(404, "progression.slot_not_found"), "notOwned"],
    [api(422, "progression.item_slot_mismatch"), "slotMismatch"],
    [new NetworkError(), "network"],
    [api(500, "x"), "other"],
  ])("%#", (error, key) => {
    expect(equipErrorKey(error)).toBe(key);
  });
});
