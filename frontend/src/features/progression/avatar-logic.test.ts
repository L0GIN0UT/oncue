import { describe, expect, it } from "vitest";
import {
  avatarReasonKey,
  avatarUploadErrorKey,
  formatBytes,
  validateAvatarFile,
} from "./avatar-logic";
import { ApiError, NetworkError } from "@/shared/api/errors";

const limits = { formats: ["jpeg", "png", "webp"], max_bytes: 1_000_000 };
const JPEG = [0xff, 0xd8, 0xff, 0xe0, 0, 0x10, 0x4a, 0x46, 0x49, 0x46, 0, 1];
const PNG = [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0, 0, 0, 13];
const WEBP = [
  ...Array.from("RIFF", (c) => c.charCodeAt(0)),
  1,
  2,
  3,
  4,
  ...Array.from("WEBP", (c) => c.charCodeAt(0)),
];
const GIF = Array.from("GIF89a......", (c) => c.charCodeAt(0));

/** Файл с заданным содержимым; `size` переопределяет заявленный размер. */
const file = (name: string, type: string, bytes: number[] | string, size?: number) => {
  const f = new File([typeof bytes === "string" ? bytes : new Uint8Array(bytes)], name, { type });
  if (size !== undefined) Object.defineProperty(f, "size", { value: size });
  return f;
};

describe("validateAvatarFile: формат по сигнатуре, а не по имени и MIME", () => {
  it.each([
    ["a.png", "image/png", PNG],
    ["a.jpg", "image/jpeg", JPEG],
    ["a.webp", "image/webp", WEBP],
  ])("%s допустим", async (name, type, bytes) => {
    expect(await validateAvatarFile(file(name, type, bytes), limits)).toBeNull();
  });

  it(".jpg с PNG-содержимым — это png: проходит, если png разрешён, и режется, если нет", async () => {
    const liar = file("photo.jpg", "image/jpeg", PNG);
    expect(await validateAvatarFile(liar, limits)).toBeNull();
    expect(await validateAvatarFile(liar, { ...limits, formats: ["jpeg"] })).toBe("format");
  });

  it(".png с текстом внутри отклоняется, MIME и расширение не авторитетны", async () => {
    expect(await validateAvatarFile(file("a.png", "image/png", "hello world!"), limits)).toBe(
      "format",
    );
    expect(await validateAvatarFile(file("a.png", "image/png", "<svg></svg>"), limits)).toBe(
      "format",
    );
  });

  it("gif и RIFF не-WEBP отклоняются", async () => {
    expect(await validateAvatarFile(file("a.gif", "image/gif", GIF), limits)).toBe("format");
    const wav = [...WEBP.slice(0, 8), ...Array.from("WAVE", (c) => c.charCodeAt(0))];
    expect(await validateAvatarFile(file("a.webp", "image/webp", wav), limits)).toBe("format");
  });

  it("без MIME и расширения формат определяется по содержимому", async () => {
    expect(await validateAvatarFile(file("noext", "", JPEG), limits)).toBeNull();
  });

  it("слишком большой файл", async () => {
    expect(await validateAvatarFile(file("a.png", "image/png", PNG, 1_000_001), limits)).toBe(
      "tooBig",
    );
  });

  it("ровно по лимиту проходит", async () => {
    expect(await validateAvatarFile(file("a.png", "image/png", PNG, 1_000_000), limits)).toBeNull();
  });

  it("пустой файл отклоняется", async () => {
    expect(await validateAvatarFile(file("a.png", "image/png", [], 0), limits)).toBe("invalid");
  });

  it("слишком короткий файл (меньше сигнатуры) — формат", async () => {
    expect(await validateAvatarFile(file("a.png", "image/png", [0x89, 0x50]), limits)).toBe(
      "format",
    );
  });
});

describe("avatarReasonKey", () => {
  it.each(["inappropriate", "copyright", "low_quality", "personal_data", "other"])(
    "известный код %s — своя подпись",
    (code) => {
      expect(avatarReasonKey(code)).toBe(`avatar.reason.${code}`);
    },
  );

  it.each([null, undefined, "", "weird_code", "__proto__"])(
    "неизвестный код (%s) — общий текст, сам код не показывается",
    (code) => {
      expect(avatarReasonKey(code)).toBeNull();
    },
  );
});

describe("avatarUploadErrorKey", () => {
  const api = (s: number, c: string) => new ApiError(s, { code: c, title: c, status: s } as never);
  it.each([
    [api(413, "ugc.too_large"), "tooLarge"],
    [api(422, "ugc.invalid_image"), "invalid"],
    [api(429, "rate_limited"), "rateLimited"],
    [api(403, "progression.avatar_not_entitled"), "notEntitled"],
    [api(503, "progression.avatar_unavailable"), "unavailable"],
    [new NetworkError(), "network"],
    [api(500, "x"), "other"],
  ])("%#", (e, key) => {
    expect(avatarUploadErrorKey(e)).toBe(key);
  });
});

describe("formatBytes", () => {
  it("килобайты и мегабайты", () => {
    expect(formatBytes(500)).toBe("500 Б");
    expect(formatBytes(2048)).toBe("2 КБ");
    expect(formatBytes(1_500_000)).toBe("1,4 МБ");
  });
});
