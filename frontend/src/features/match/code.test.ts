import { describe, expect, it } from "vitest";
import { isRoomCode, normalizeRoomCode } from "./code";

describe("код комнаты", () => {
  it("нормализует регистр и разделители", () => {
    expect(normalizeRoomCode(" abcd-efgh ")).toBe("ABCDEFGH");
    expect(normalizeRoomCode("abcd efgh")).toBe("ABCDEFGH");
  });

  it("достаёт код из ссылки на комнату", () => {
    expect(normalizeRoomCode("https://oncue.example/room/k7mq2xwp?x=1")).toBe("K7MQ2XWP");
  });

  it("принимает только алфавит без 0/O/1/I и ровно 8 символов", () => {
    expect(isRoomCode("K7MQ2XWP")).toBe(true);
    for (const bad of ["K7MQ2XW", "K7MQ2XWPP", "K7MQ2XW0", "K7MQ2XWO", "K7MQ2XW1", "K7MQ2XWI"])
      expect(isRoomCode(bad)).toBe(false);
    expect(isRoomCode(normalizeRoomCode("k7mq2xwp"))).toBe(true);
  });
});
