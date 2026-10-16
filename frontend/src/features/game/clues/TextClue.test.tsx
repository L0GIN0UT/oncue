import { describe, expect, it } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "@/test/render";
import type { LoadedClue } from "../use-clue";
import { clueRegistry } from "./registry";
import { TextClue } from "./TextClue";
import { emojiGlyphs, parseTextAsset, TextAssetError } from "./text-asset";

const clue = (body: unknown, level = 1): LoadedClue => ({
  level,
  kind: "text",
  url: "/media/a/x.json",
  expires_at: "2026-10-10T09:01:00Z",
  mime: "application/json",
  meta: { fields: ["tagline"] },
  objectUrl: `blob:text-${level}-${JSON.stringify(body).length}`,
  blob: new Blob([JSON.stringify(body)], { type: "application/json" }),
});

describe("text_asset.v1", () => {
  it("разбирает блоки и пропускает неизвестные виды и пустые поля", () => {
    const asset = parseTextAsset({
      version: 1,
      blocks: [
        { kind: "tagline", text: "Слоган" },
        { kind: "future_kind", text: "?" },
        { kind: "plot", text: "  " },
        {
          kind: "fields",
          items: [
            { field: "year", value: "1979" },
            { field: "unknown", value: "x" },
          ],
        },
      ],
    });
    expect(asset.blocks).toEqual([
      { kind: "tagline", text: "Слоган" },
      { kind: "fields", items: [{ field: "year", value: "1979" }] },
    ]);
  });

  it("чужая версия или ни одного показываемого блока — ошибка ассета", () => {
    expect(() => parseTextAsset({ version: 2, blocks: [] })).toThrow(TextAssetError);
    expect(() => parseTextAsset({ version: 1, blocks: [{ kind: "plot" }] })).toThrow(
      TextAssetError,
    );
  });

  it("эмодзи делятся по графемам: составной эмодзи — один символ", () => {
    expect(emojiGlyphs("🎬👨‍👩‍👧🇷🇺 ")).toEqual(["🎬", "👨‍👩‍👧", "🇷🇺"]);
  });
});

describe("TextClue", () => {
  it("реестр подачи полный: text — TextClue", () => {
    expect(clueRegistry.text).toBe(TextClue);
  });

  it("описание: слоган, сюжет и поля уровня из Blob", async () => {
    renderWithProviders(
      <TextClue
        clue={clue({
          version: 1,
          blocks: [
            { kind: "tagline", text: "Синтетическая история №7" },
            { kind: "plot", text: "Герой ███ уходит в Зону." },
            {
              kind: "fields",
              items: [
                { field: "year", value: "1979" },
                { field: "director", value: "Андрей Тарковский" },
              ],
            },
          ],
        })}
      />,
    );
    const text = await screen.findByTestId("text-clue");
    expect(text).toHaveTextContent("Синтетическая история №7");
    expect(text).toHaveTextContent("Герой ███ уходит в Зону.");
    expect(screen.getByText("Год").nextElementSibling).toHaveTextContent("1979");
    expect(screen.getByText("Режиссёр").nextElementSibling).toHaveTextContent("Андрей Тарковский");
  });

  it("эмодзи — крупно, с подписью для скринридера", async () => {
    renderWithProviders(
      <TextClue clue={clue({ version: 1, blocks: [{ kind: "emoji", text: "🌊🚀🧠" }] })} />,
    );
    const emoji = await screen.findByRole("img", { name: "Эмодзи (3): 🌊 🚀 🧠" });
    expect(emoji).toHaveAttribute("data-testid", "emoji-clue");
  });

  it("испорченный ассет — сообщение об ошибке вместо загадки", async () => {
    renderWithProviders(<TextClue clue={clue({ blocks: "nope" })} />);
    expect(await screen.findByText("Фрагмент не загрузился")).toBeInTheDocument();
    expect(screen.queryByTestId("text-clue")).not.toBeInTheDocument();
  });
});
