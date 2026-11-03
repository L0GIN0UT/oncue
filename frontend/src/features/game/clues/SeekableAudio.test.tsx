import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "@/test/render";
import { SeekableAudio } from "./SeekableAudio";

const RECT = { left: 0, width: 300, right: 300 } as DOMRect;

afterEach(() => vi.restoreAllMocks());

function setup(seconds: number | null, fileDuration = 50) {
  const play = vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue(undefined);
  vi.spyOn(HTMLMediaElement.prototype, "duration", "get").mockReturnValue(fileDuration);
  renderWithProviders(<SeekableAudio objectUrl="blob:test/1" seconds={seconds} />);
  const audio = screen.getByTestId("clue-audio") as HTMLAudioElement;
  fireEvent.loadedMetadata(audio);
  const bar = screen.getByRole("slider");
  vi.spyOn(bar, "getBoundingClientRect").mockReturnValue(RECT);
  return { audio, bar, play };
}

/** Клик на `second`-й секунде полосы в 30 с (ширина 300 px → 10 px на секунду). */
const clickAt = (bar: HTMLElement, second: number) =>
  fireEvent.pointerDown(bar, { clientX: second * 10 });

describe("SeekableAudio: перемотка", () => {
  it("на уровне: клик внутри открытой части перематывает", () => {
    const { audio, bar } = setup(4);
    clickAt(bar, 3);
    expect(audio.currentTime).toBe(3);
  });

  it("на уровне: клик дальше открытого фрагмента прижимается к его границе", () => {
    const { audio, bar } = setup(4);
    clickAt(bar, 25);
    expect(audio.currentTime).toBe(4);
    expect(bar).toHaveAttribute("aria-valuemax", "4");
  });

  it("файл клипа 50 с, уровень без границы — не дальше 30 с", () => {
    const { audio, bar } = setup(null, 50);
    clickAt(bar, 30);
    expect(audio.currentTime).toBe(30);
    expect(bar).toHaveAttribute("aria-valuemax", "30");
  });

  it("на ответе раунда весь фрагмент открыт: клик на 24-й секунде", () => {
    const { audio, bar } = setup(null);
    clickAt(bar, 24);
    expect(audio.currentTime).toBe(24);
  });

  it("стрелка вправо на границе уровня не выходит за неё", async () => {
    const user = userEvent.setup();
    const { audio, bar } = setup(2);
    bar.focus();
    await user.keyboard("{ArrowRight}{ArrowRight}{ArrowRight}");
    expect(audio.currentTime).toBe(2);
  });

  it("Play после перемотки продолжает с этого места, ссылка не меняется", async () => {
    const user = userEvent.setup();
    const { audio, bar, play } = setup(null);
    clickAt(bar, 12);
    await user.click(screen.getByRole("button"));
    expect(play).toHaveBeenCalledTimes(1);
    expect(audio.currentTime).toBe(12);
    expect(audio.getAttribute("src")).toBe("blob:test/1");
  });

  it("с конца открытой части Play начинает сначала", async () => {
    const user = userEvent.setup();
    const { audio, bar } = setup(4);
    clickAt(bar, 4);
    expect(audio.currentTime).toBe(4);
    await user.click(screen.getByRole("button"));
    expect(audio.currentTime).toBe(0);
  });
});

describe("SeekableAudio: проход до границы и прогресс", () => {
  function playerWithProgress(seconds: number | null) {
    const onProgress = vi.fn();
    const play = vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue(undefined);
    const pause = vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => {});
    vi.spyOn(HTMLMediaElement.prototype, "duration", "get").mockReturnValue(50);
    renderWithProviders(
      <SeekableAudio objectUrl="blob:test/1" seconds={seconds} onProgress={onProgress} />,
    );
    const audio = screen.getByTestId("clue-audio") as HTMLAudioElement;
    fireEvent.loadedMetadata(audio);
    return { audio, onProgress, play, pause };
  }

  it("timeupdate за границей уровня: пауза, один проход без повтора, прогресс сбрасывается", async () => {
    const user = userEvent.setup();
    const { audio, onProgress, pause } = playerWithProgress(4);
    await user.click(screen.getByRole("button"));
    vi.spyOn(audio, "paused", "get").mockReturnValue(false);
    audio.currentTime = 4.2;
    fireEvent.timeUpdate(audio);
    expect(pause).toHaveBeenCalled();
    expect(audio.loop).toBe(false);
    expect(onProgress).toHaveBeenLastCalledWith(null);
    expect(screen.getByRole("button", { name: /Слушать/ })).toBeInTheDocument();
  });

  it("конец файла (ended) останавливает воспроизведение", async () => {
    const user = userEvent.setup();
    const { audio, onProgress } = playerWithProgress(null);
    await user.click(screen.getByRole("button"));
    expect(screen.getByRole("button", { name: "Пауза" })).toBeInTheDocument();
    fireEvent.ended(audio);
    expect(onProgress).toHaveBeenLastCalledWith(null);
    expect(screen.queryByRole("button", { name: "Пауза" })).not.toBeInTheDocument();
  });

  it("перемотка во время игры сообщает долю от открытой части", async () => {
    const user = userEvent.setup();
    const { onProgress } = playerWithProgress(10);
    await user.click(screen.getByRole("button"));
    const bar = screen.getByRole("slider");
    vi.spyOn(bar, "getBoundingClientRect").mockReturnValue(RECT);
    fireEvent.pointerDown(bar, { clientX: 50 }); // 5 с из 10 открытых
    expect(onProgress).toHaveBeenLastCalledWith(0.5);
  });

  it("до загрузки метаданных (duration = NaN) граница — 30 с", () => {
    vi.spyOn(HTMLMediaElement.prototype, "duration", "get").mockReturnValue(Number.NaN);
    renderWithProviders(<SeekableAudio objectUrl="blob:test/1" seconds={null} />);
    expect(screen.getByRole("slider")).toHaveAttribute("aria-valuemax", "30");
  });

  it("второй клик по Play, пока play() не ответил, не ставит звук на паузу", async () => {
    const user = userEvent.setup();
    let resolve!: () => void;
    const play = vi
      .spyOn(HTMLMediaElement.prototype, "play")
      .mockImplementation(() => new Promise<void>((r) => (resolve = r)));
    const pause = vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => {});
    renderWithProviders(<SeekableAudio objectUrl="blob:test/1" seconds={null} />);
    const button = screen.getByRole("button");
    await user.click(button);
    await user.click(button);
    expect(play).toHaveBeenCalledTimes(1);
    expect(pause).not.toHaveBeenCalled();
    resolve();
    expect(await screen.findByRole("button", { name: "Пауза" })).toBeInTheDocument();
  });
});
