import { describe, expect, it } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "@/test/render";
import type { LoadedClue } from "../use-clue";
import { ImageClue, imageHintParts } from "./ImageClue";

const clue = (meta: LoadedClue["meta"]): LoadedClue => ({
  level: 1,
  kind: "image",
  url: "/media/a/x.webp",
  expires_at: "2026-10-10T09:01:00Z",
  mime: "image/webp",
  meta,
  objectUrl: "blob:frame-1",
  blob: new Blob(),
});

describe("ImageClue", () => {
  it("техническая подпись (обрезка, размытие, пиксели) игроку не показывается", () => {
    renderWithProviders(<ImageClue clue={clue({ blur_px: 48, crop: 0.35, pixel_block: 16 })} />);
    expect(screen.queryByTestId("clue-image-hint")).not.toBeInTheDocument();
    expect(document.body).not.toHaveTextContent(/размытие|Видно \d+% кадра|Пиксели/);
    expect(imageHintParts({ blur_px: 48, crop: 0.35, pixel_block: 16 })).toEqual([]);
  });

  it("последовательность кадров — уровень открывает другой кадр (meta.frame)", () => {
    expect(imageHintParts({ frame: 3 })).toEqual([{ key: "game.frameNumber", frame: 3 }]);
    renderWithProviders(<ImageClue clue={clue({ frame: 2 })} />);
    expect(screen.getByTestId("clue-image-hint")).toHaveTextContent("Кадр 2");
  });
});
