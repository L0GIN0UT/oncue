import { afterEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "@/test/render";
import { SeekableAudio } from "./SeekableAudio";
import type { TapeSeekState } from "./types";

afterEach(() => vi.restoreAllMocks());

/** Перемотка идёт снаружи — по ленте подсказок: плеер отдаёт ей `seek` через `onSeekState`. */
function setup(seconds: number | null, fileDuration = 50) {
  const play = vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue(undefined);
  vi.spyOn(HTMLMediaElement.prototype, "duration", "get").mockReturnValue(fileDuration);
  const onSeekState = vi.fn();
  renderWithProviders(
    <SeekableAudio objectUrl="blob:test/1" seconds={seconds} onSeekState={onSeekState} />,
  );
  const audio = screen.getByTestId("clue-audio") as HTMLAudioElement;
  fireEvent.loadedMetadata(audio);
  const state = () => onSeekState.mock.calls.at(-1)![0] as TapeSeekState;
  return { audio, state, play };
}

describe("SeekableAudio: перемотка", () => {
  it("на уровне: перемотка внутри открытой части", () => {
    const { audio, state } = setup(4);
    act(() => state().seek(3));
    expect(audio.currentTime).toBe(3);
  });

  it("на уровне: дальше открытого фрагмента прижимается к его границе", () => {
    const { audio, state } = setup(4);
    act(() => state().seek(25));
    expect(audio.currentTime).toBe(4);
    expect(state().open).toBe(4);
  });

  it("файл клипа 50 с, уровень без границы — не дальше 30 с", () => {
    const { audio, state } = setup(null, 50);
    act(() => state().seek(40));
    expect(audio.currentTime).toBe(30);
    expect(state().open).toBe(30);
  });

  it("Play после перемотки продолжает с этого места, ссылка не меняется", async () => {
    const user = userEvent.setup();
    const { audio, state, play } = setup(null);
    act(() => state().seek(12));
    await user.click(screen.getByRole("button"));
    expect(play).toHaveBeenCalledTimes(1);
    expect(audio.currentTime).toBe(12);
    expect(audio.getAttribute("src")).toBe("blob:test/1");
  });

  it("с конца открытой части Play начинает сначала", async () => {
    const user = userEvent.setup();
    const { audio, state } = setup(4);
    act(() => state().seek(4));
    expect(audio.currentTime).toBe(4);
    await user.click(screen.getByRole("button"));
    expect(audio.currentTime).toBe(0);
  });

  it("в самом плеере полосы перемотки нет", () => {
    setup(4);
    expect(screen.queryByRole("slider")).not.toBeInTheDocument();
  });
});

describe("SeekableAudio: проход до границы и прогресс", () => {
  function playerWithProgress(seconds: number | null) {
    const onProgress = vi.fn();
    const onSeekState = vi.fn();
    const play = vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue(undefined);
    const pause = vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => {});
    vi.spyOn(HTMLMediaElement.prototype, "duration", "get").mockReturnValue(50);
    renderWithProviders(
      <SeekableAudio
        objectUrl="blob:test/1"
        seconds={seconds}
        onProgress={onProgress}
        onSeekState={onSeekState}
      />,
    );
    const audio = screen.getByTestId("clue-audio") as HTMLAudioElement;
    fireEvent.loadedMetadata(audio);
    return { audio, onProgress, play, pause, onSeekState };
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
    const { onProgress, onSeekState } = playerWithProgress(10);
    await user.click(screen.getByRole("button"));
    const { seek } = onSeekState.mock.calls.at(-1)![0] as TapeSeekState;
    act(() => seek(5)); // 5 с из 10 открытых
    expect(onProgress).toHaveBeenLastCalledWith(0.5);
  });

  it("до загрузки метаданных (duration = NaN) граница — 30 с", () => {
    vi.spyOn(HTMLMediaElement.prototype, "duration", "get").mockReturnValue(Number.NaN);
    const onSeekState = vi.fn();
    renderWithProviders(
      <SeekableAudio objectUrl="blob:test/1" seconds={null} onSeekState={onSeekState} />,
    );
    expect(onSeekState.mock.calls.at(-1)![0]).toMatchObject({ open: 30 });
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

describe("SeekableAudio: перемотка по ленте подсказок", () => {
  it("на уровне мини-перемотки нет, а состояние для ленты отдаётся", () => {
    const onSeekState = vi.fn();
    vi.spyOn(HTMLMediaElement.prototype, "duration", "get").mockReturnValue(50);
    renderWithProviders(
      <SeekableAudio objectUrl="blob:test/1" seconds={4} onSeekState={onSeekState} />,
    );
    fireEvent.loadedMetadata(screen.getByTestId("clue-audio"));
    expect(screen.queryByRole("slider")).not.toBeInTheDocument();
    const state = onSeekState.mock.calls.at(-1)?.[0];
    expect(state).toMatchObject({ time: 0, open: 4 });
    act(() => state.seek(25)); // дальше открытой части: к границе
    expect((screen.getByTestId("clue-audio") as HTMLAudioElement).currentTime).toBe(4);
  });

  it("размонтирование сообщает null", () => {
    const onSeekState = vi.fn();
    const { unmount } = renderWithProviders(
      <SeekableAudio objectUrl="blob:test/1" seconds={4} onSeekState={onSeekState} />,
    );
    unmount();
    expect(onSeekState).toHaveBeenLastCalledWith(null);
  });
});
