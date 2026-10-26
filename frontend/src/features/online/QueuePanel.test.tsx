import { describe, expect, it, vi } from "vitest";
import { http, HttpResponse } from "msw";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { server, setupMockBackend } from "@/test/msw";
import { renderWithProviders } from "@/test/render";
import { QueuePanel } from "./QueuePanel";

const push = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace: vi.fn(), push }) }));

setupMockBackend();

const Q = "*/api/match/v1/queue";
const QUEUES = [
  { ladder: "film", format: "classic" as const, searching: 3 },
  { ladder: "film", format: "lite" as const, searching: 0 },
  { ladder: "music", format: "classic" as const, searching: 1 },
];

const ticket = (patch: Record<string, unknown> = {}) => ({
  ladder: "film",
  format: "classic",
  state: "searching",
  entered_at: "2026-10-23T10:00:00Z",
  wait_s: 0,
  window: 100,
  slow: false,
  room_code: null,
  // Период heartbeat из ответа сервера (в тестах — доли секунды).
  tick_s: 0.04,
  ...patch,
});

const problem = (status: number, code: string, details?: Record<string, unknown>) =>
  HttpResponse.json({ type: "x", title: "x", status, code, details }, { status });

/** Записи в очереди нет (первый tick при загрузке хаба). */
const notInQueue = () => http.post(`${Q}/tick`, () => problem(404, "match.not_in_queue"));

describe("QueuePanel — очередь рейтинговой дуэли", () => {
  it("раздел переключается, формат выбирается чипом, одна кнопка «Найти соперника»", async () => {
    server.use(notInQueue());
    renderWithProviders(<QueuePanel queues={QUEUES} />);
    // Первый раздел из данных; у кино два формата — чипы, у музыки один — чипов нет.
    expect(screen.getByTestId("queue-enter-film-classic")).toHaveTextContent("Найти соперника");
    expect(screen.getByTestId("queue-format-film-classic")).toHaveTextContent("Классика");
    expect(screen.getByTestId("queue-format-film-lite")).toHaveTextContent("Лайт");
    expect(screen.getByTestId("queue-hint")).toHaveTextContent("Ищут 3 игрока");
    await userEvent.click(screen.getByTestId("queue-format-film-lite"));
    expect(screen.getByTestId("queue-enter-film-lite")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("radio", { name: "Музыка" }));
    expect(screen.getByTestId("queue-enter-music-classic")).toBeEnabled();
    expect(screen.queryByTestId(/queue-format-/)).not.toBeInTheDocument();
    expect(screen.getByTestId("queue-hint")).toHaveTextContent("Ищет 1 игрок");
  });

  it("рейтинг раздела: очки, звание, путь до следующего; калибровка вместо звания", () => {
    server.use(notInQueue());
    const rating = (patch: Record<string, unknown>) => ({
      calibrating: false,
      calibration_left: 0,
      ladder: "film",
      matches_played: 12,
      next_tier_at: 1600,
      place: null,
      rating: 1480,
      tier: { title_key: "rating.tier.silver", step: 2 },
      ...patch,
    });
    const { unmount } = renderWithProviders(
      <QueuePanel queues={QUEUES} ratings={[rating({})] as never} />,
    );
    expect(screen.getByTestId("rating-film")).toHaveTextContent("1480");
    expect(screen.getByTestId("tier")).toHaveTextContent("Серебро 2");
    expect(screen.getByTestId("next-tier")).toHaveTextContent("До следующего звания: 120");
    unmount();
    renderWithProviders(
      <QueuePanel
        queues={QUEUES}
        ratings={
          [rating({ calibrating: true, calibration_left: 7, rating: null, tier: null })] as never
        }
      />,
    );
    expect(screen.getByTestId("calibration")).toHaveTextContent("Калибровка: осталось 7 матчей");
    expect(screen.queryByTestId("tier")).not.toBeInTheDocument();
  });

  it("ожидание: подсказка «ищем дольше обычного» из ответа сервера, отмена", async () => {
    let left = 0;
    server.use(
      notInQueue(),
      // Большой период heartbeat: тест не зависит от таймеров (heartbeat — в use-queue.test.tsx).
      http.post(`${Q}/enter`, () =>
        HttpResponse.json(ticket({ format: "lite", slow: true, wait_s: 45, tick_s: 60 })),
      ),
      http.post(`${Q}/leave`, () => {
        left += 1;
        return new HttpResponse(null, { status: 204 });
      }),
    );
    renderWithProviders(<QueuePanel queues={QUEUES} />);
    await userEvent.click(screen.getByTestId("queue-format-film-lite"));
    await userEvent.click(screen.getByTestId("queue-enter-film-lite"));
    expect(await screen.findByTestId("queue-searching")).toHaveTextContent(
      "Ищем соперника: Кино · Лайт",
    );
    expect(screen.getByTestId("queue-slow")).toHaveTextContent("дольше обычного");
    // Пока ищем, вторая очередь недоступна: кнопка и чипы заблокированы.
    expect(screen.getByTestId("queue-enter-film-lite")).toBeDisabled();
    expect(screen.getByTestId("queue-format-film-classic")).toBeDisabled();

    await userEvent.click(screen.getByTestId("queue-cancel"));
    await waitFor(() => expect(screen.queryByTestId("queue-searching")).not.toBeInTheDocument());
    expect(left).toBe(1);
    expect(screen.getByTestId("queue-enter-film-lite")).toBeEnabled();
  });

  it("матч найден: сигнал на экране и переход в комнату", async () => {
    push.mockClear();
    server.use(
      notInQueue(),
      http.post(`${Q}/enter`, () =>
        HttpResponse.json(ticket({ state: "matched", room_code: "K7MQ2XWP" })),
      ),
    );
    renderWithProviders(<QueuePanel queues={QUEUES} />);
    await userEvent.click(screen.getByTestId("queue-enter-film-classic"));
    expect(await screen.findByTestId("queue-found")).toHaveTextContent("Соперник найден");
    await waitFor(() => expect(push).toHaveBeenCalledWith("/room/K7MQ2XWP"), { timeout: 3000 });
  });

  it("перезагрузка во время поиска: первый tick восстанавливает ожидание", async () => {
    server.use(http.post(`${Q}/tick`, () => HttpResponse.json(ticket({ wait_s: 12 }))));
    renderWithProviders(<QueuePanel queues={QUEUES} />);
    expect(await screen.findByTestId("queue-searching")).toHaveTextContent("Ждём 12 с");
  });

  it("ошибки входа: нет прав на рейтинг, уже в комнате — со ссылкой на неё", async () => {
    server.use(
      notInQueue(),
      http.post(`${Q}/enter`, () =>
        problem(409, "match.active_room_conflict", { code: "ABCD2345" }),
      ),
    );
    renderWithProviders(<QueuePanel queues={QUEUES} />);
    await userEvent.click(screen.getByRole("radio", { name: "Музыка" }));
    await userEvent.click(screen.getByTestId("queue-enter-music-classic"));
    expect(await screen.findByRole("alert")).toHaveTextContent("Вы уже в другой комнате");
    expect(screen.getByRole("link", { name: "Перейти в комнату" })).toHaveAttribute(
      "href",
      "/room/ABCD2345",
    );
    await userEvent.click(screen.getByRole("button", { name: "Закрыть" }));
    expect(screen.queryByTestId("queue-error")).not.toBeInTheDocument();
  });

  it("блок queues недоступен — понятное сообщение, без кнопок", () => {
    server.use(notInQueue());
    renderWithProviders(<QueuePanel queues={null} />);
    expect(screen.getByTestId("queue-unavailable")).toBeInTheDocument();
    expect(screen.queryByTestId(/queue-enter/)).not.toBeInTheDocument();
  });

  it("доступность: во время поиска нет вложенных role=status, фокус переходит на «Отменить» и возвращается на кнопку входа", async () => {
    server.use(
      notInQueue(),
      http.post(`${Q}/enter`, () => HttpResponse.json(ticket({ tick_s: 60 }))),
      http.post(`${Q}/leave`, () => new HttpResponse(null, { status: 204 })),
    );
    renderWithProviders(<QueuePanel queues={QUEUES} />);
    await userEvent.click(screen.getByTestId("queue-enter-film-classic"));
    const searching = await screen.findByTestId("queue-searching");
    expect(searching.querySelector("[role=status]")).toBeNull();
    await waitFor(() => expect(screen.getByTestId("queue-cancel")).toHaveFocus());
    await userEvent.click(screen.getByTestId("queue-cancel"));
    await waitFor(() => expect(screen.getByTestId("queue-enter-film-classic")).toHaveFocus());
  });

  it("провал отмены: поиск остаётся, показано сообщение", async () => {
    server.use(
      notInQueue(),
      http.post(`${Q}/enter`, () => HttpResponse.json(ticket({ tick_s: 60 }))),
      http.post(`${Q}/leave`, () => problem(503, "match.game_unavailable")),
    );
    renderWithProviders(<QueuePanel queues={QUEUES} />);
    await userEvent.click(screen.getByTestId("queue-enter-film-classic"));
    await userEvent.click(await screen.findByTestId("queue-cancel"));
    expect(await screen.findByTestId("queue-notice")).toBeInTheDocument();
    expect(screen.getByTestId("queue-searching")).toBeInTheDocument();
    expect(screen.getByTestId("queue-cancel")).toBeEnabled();
  });

  it("404 на heartbeat: сообщение «вас убрали из очереди»", async () => {
    let ticks = 0;
    server.use(
      http.post(`${Q}/enter`, () => HttpResponse.json(ticket())),
      http.post(`${Q}/tick`, () => {
        ticks += 1;
        return ticks === 1 || ticks > 1
          ? problem(404, "match.not_in_queue")
          : HttpResponse.json(ticket());
      }),
    );
    renderWithProviders(<QueuePanel queues={QUEUES} />);
    await userEvent.click(screen.getByTestId("queue-enter-film-classic"));
    expect(await screen.findByRole("alert", {}, { timeout: 4000 })).toHaveTextContent(
      "Вас убрали из очереди",
    );
  });
});
