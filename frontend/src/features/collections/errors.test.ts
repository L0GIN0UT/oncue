import { describe, expect, it } from "vitest";
import { createTranslator } from "next-intl";
import messages from "../../../messages/ru.json";
import { ApiError } from "@/shared/api/errors";
import { apiErrorText, errorText, fieldErrorsOf, fieldOf } from "./errors";

const t = createTranslator({ locale: "ru", messages }) as unknown as Parameters<
  typeof errorText
>[0];

describe("ошибки сборников", () => {
  it("путь → поле конструктора", () => {
    expect(fieldOf("title")).toEqual({ kind: "title" });
    expect(fieldOf("cover.preset")).toEqual({ kind: "cover" });
    expect(fieldOf("tags[2]")).toEqual({ kind: "tags" });
    expect(fieldOf("default_mode_key")).toEqual({ kind: "mode" });
    expect(fieldOf("items")).toEqual({ kind: "items" });
    expect(fieldOf("items[3]")).toEqual({ kind: "item", index: 3, emoji: false });
    expect(fieldOf("items[0].custom.emoji")).toEqual({ kind: "item", index: 0, emoji: true });
  });

  it("код и причина ugc.* → текст; неизвестный код — общий текст", () => {
    expect(errorText(t, { code: "collections.title_invalid", reason: "ugc.link_forbidden" })).toBe(
      "Название не подходит: ссылки запрещены",
    );
    expect(errorText(t, { code: "collections.items_too_few", min: 5 })).toBe(
      "Слишком мало единиц: нужно не меньше 5",
    );
    expect(errorText(t, { code: "collections.something_new" })).toBe(
      "Что-то пошло не так. Попробуйте ещё раз.",
    );
    expect(errorText(t, { code: "<script>" })).toBe("Что-то пошло не так. Попробуйте ещё раз.");
  });

  it("details.errors разбирается без доверия к форме", () => {
    const e = new ApiError(422, {
      code: "collections.publish_invalid",
      details: {
        errors: [
          { path: "items[1]", code: "collections.item_unavailable" },
          { path: 5, code: "x" },
          null,
          { path: "title", code: "collections.title_invalid", reason: "ugc.too_long" },
        ],
      },
    });
    expect(fieldErrorsOf(e)).toEqual([
      { path: "items[1]", code: "collections.item_unavailable" },
      { path: "title", code: "collections.title_invalid", reason: "ugc.too_long" },
    ]);
    expect(
      apiErrorText(
        t,
        new ApiError(409, { code: "collections.drafts_limit", details: { max: 20 } }),
      ),
    ).toBe("Слишком много черновиков: не больше 20. Опубликуйте или удалите лишние");
    expect(apiErrorText(t, new Error("offline"))).toBe(
      "Нет связи с сервером. Проверьте соединение.",
    );
  });
});
