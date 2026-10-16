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
  it("подпись уровня кадра — из meta.crop и meta.blur_px ядра", () => {
    renderWithProviders(<ImageClue clue={clue({ blur_px: 48, crop: 0.35 })} />);
    expect(screen.getByTestId("clue-image-hint")).toHaveTextContent(
      "Видно 35% кадра · размытие 48 px",
    );
  });

  it("постер — meta.pixel_block; последний уровень без подписи", () => {
    expect(imageHintParts({ pixel_block: 16 })).toEqual([{ key: "game.posterPixels", px: 16 }]);
    renderWithProviders(<ImageClue clue={clue({ blur_px: 0, crop: 1 })} />);
    expect(screen.queryByTestId("clue-image-hint")).not.toBeInTheDocument();
  });

  it("последовательность кадров — уровень открывает другой кадр (meta.frame)", () => {
    expect(imageHintParts({ frame: 3 })).toEqual([{ key: "game.frameNumber", frame: 3 }]);
    renderWithProviders(<ImageClue clue={clue({ frame: 2 })} />);
    expect(screen.getByTestId("clue-image-hint")).toHaveTextContent("Кадр 2");
  });
});
