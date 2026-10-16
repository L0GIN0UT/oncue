import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { server, signInAsGuest, setupMockBackend, world } from "@/test/msw";
import { renderWithProviders } from "@/test/render";
import { personId } from "../../../e2e/mocks/world";
import { StartGame } from "./StartGame";
import { toggleSubject } from "@/features/solo/SubjectPicker";

const replace = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace, push: vi.fn() }) }));

setupMockBackend();

/** Запросы к API во время теста (метод и путь с query). */
function recordRequests(): string[] {
  const seen: string[] = [];
  server.events.on("request:start", ({ request }) => {
    const url = new URL(request.url);
    seen.push(`${request.method} ${url.pathname}${url.search}`);
  });
  return seen;
}

describe("StartGame", () => {
  beforeEach(() => {
    replace.mockClear();
    server.events.removeAllListeners();
  });

  it("режим без выбора стартует сразу", async () => {
    signInAsGuest();
    renderWithProviders(<StartGame modeKey="film.pair.rating" presetId={null} />);
    await waitFor(() =>
      expect(replace).toHaveBeenCalledWith(expect.stringMatching(/^\/play\/s\//)),
    );
    expect(screen.queryByTestId("subject-picker")).not.toBeInTheDocument();
  });

  it("марафон: выбор режиссёра (поиск с паузой 200 мс) → POST с subject_ids", async () => {
    const user = userEvent.setup();
    signInAsGuest();
    const requests = recordRequests();
    renderWithProviders(<StartGame modeKey="film.marathon.director" presetId={null} />);
    const picker = await screen.findByTestId("subject-picker");
    expect(picker).toHaveTextContent("Марафон режиссёра");
    expect(screen.getByTestId("subject-start")).toBeDisabled();
    // Пустой запрос — персоны с наибольшим числом фильмов.
    const tarkovsky = personId("Андрей Тарковский");
    expect(await screen.findByTestId(`subject-option-${tarkovsky}`)).toHaveTextContent("5 работ");

    await user.type(screen.getByTestId("subject-search"), "Тарк");
    await waitFor(() =>
      expect(requests.filter((r) => r.includes("/subjects") && r.includes("q="))).toEqual([
        `GET /api/game/v1/modes/film.marathon.director/subjects?limit=20&q=${encodeURIComponent("Тарк")}`,
      ]),
    );
    await user.click(screen.getByTestId(`subject-option-${tarkovsky}`));
    expect(screen.getByTestId(`subject-option-${tarkovsky}`)).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    await user.click(screen.getByTestId("subject-start"));
    await waitFor(() =>
      expect(replace).toHaveBeenCalledWith(expect.stringMatching(/^\/play\/s\//)),
    );
    const session = [...world.sessions.values()].at(-1)!;
    expect(session.mode.key).toBe("film.marathon.director");
    expect(session.rounds.map((r) => r.itemId)).toHaveLength(5);
  });

  it("повторный вход в сыгранную сетку недели (409) ведёт на итог", async () => {
    const subject = signInAsGuest();
    const played = world.startSession(subject, world.mode("film.bracket.weekly")!, null);
    played.status = "finished";
    renderWithProviders(<StartGame modeKey="film.bracket.weekly" presetId={null} />);
    await waitFor(() => expect(replace).toHaveBeenCalledWith(`/results/${played.id}`));
  });

  it("поиск ждёт паузу ввода: несколько символов подряд — один запрос", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime, delay: 50 });
      signInAsGuest();
      const requests = recordRequests();
      renderWithProviders(<StartGame modeKey="film.marathon.director" presetId={null} />);
      const input = await screen.findByTestId("subject-search");
      await user.type(input, "Ряз");
      await act(async () => {
        await vi.advanceTimersByTimeAsync(250);
      });
      await waitFor(() => expect(requests.filter((r) => r.includes("q=")).length).toBe(1));
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("toggleSubject", () => {
  const a = { subject_id: "a", name: "A", role: "director" as const, items: 5 };
  const b = { subject_id: "b", name: "B", role: "director" as const, items: 5 };
  it("до max персон; повторный выбор снимает; при max 1 — замена", () => {
    expect(toggleSubject([], a, 2)).toEqual([a]);
    expect(toggleSubject([a], b, 2)).toEqual([a, b]);
    expect(toggleSubject([a, b], a, 2)).toEqual([b]);
    expect(toggleSubject([a], b, 1)).toEqual([b]);
    expect(toggleSubject([a, b], { ...a, subject_id: "c" }, 2)).toEqual([a, b]);
  });
});
