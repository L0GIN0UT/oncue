import { describe, expect, it, vi } from "vitest";
import { http, HttpResponse } from "msw";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { server, setupMockBackend } from "@/test/msw";
import { renderWithProviders } from "@/test/render";
import { ReturnToRoomBanner } from "./ReturnToRoomBanner";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
  usePathname: () => "/online",
}));

vi.mock("@/features/auth/session", () => ({
  useViewer: () => ({ kind: "user", pending: false }),
}));

setupMockBackend();

const active = (code: string | null, phase: string | null) =>
  http.get("*/api/match/v1/rooms/active", () => HttpResponse.json({ code, phase }));

describe("плашка «Вы в комнате»", () => {
  it("комнаты нет — плашки нет", async () => {
    server.use(active(null, null));
    renderWithProviders(<ReturnToRoomBanner />);
    await waitFor(() => expect(screen.queryByTestId("return-to-room")).not.toBeInTheDocument());
  });

  it("лобби: «Выйти» выходит сразу, без подтверждения, и плашка исчезает", async () => {
    const leaves: string[] = [];
    // Сервер с состоянием: после leave комнаты у игрока уже нет (иначе перезапрос вернул бы плашку).
    server.use(
      http.get("*/api/match/v1/rooms/active", () =>
        HttpResponse.json(
          leaves.length ? { code: null, phase: null } : { code: "K7MQ2XWP", phase: "lobby" },
        ),
      ),
      http.post("*/api/match/v1/rooms/:code/leave", ({ params }) => {
        leaves.push(String(params.code));
        return HttpResponse.json({ version: 2, room: {} });
      }),
    );
    renderWithProviders(<ReturnToRoomBanner />);
    expect(await screen.findByTestId("return-to-room")).toHaveTextContent("K7MQ2XWP");
    await userEvent.click(screen.getByTestId("return-to-room-leave"));
    await waitFor(() => expect(leaves).toEqual(["K7MQ2XWP"]));
    await waitFor(() => expect(screen.queryByTestId("return-to-room")).not.toBeInTheDocument());
  });

  it("идущий матч: «Сдаться и выйти» сначала спрашивает, выход — только после подтверждения", async () => {
    const leaves: string[] = [];
    server.use(
      active("K7MQ2XWP", "level_open"),
      http.post("*/api/match/v1/rooms/:code/leave", ({ params }) => {
        leaves.push(String(params.code));
        return HttpResponse.json({ version: 2, room: {} });
      }),
    );
    renderWithProviders(<ReturnToRoomBanner />);
    await userEvent.click(await screen.findByTestId("return-to-room-leave"));
    const dialog = await screen.findByTestId("active-room-conflict");
    expect(dialog).toHaveTextContent("сдача");
    expect(screen.queryByTestId("conflict-back")).not.toBeInTheDocument(); // «Вернуться» — в самой плашке
    expect(leaves).toEqual([]);

    await userEvent.click(screen.getByTestId("conflict-cancel"));
    expect(screen.queryByTestId("active-room-conflict")).not.toBeInTheDocument();
    expect(leaves).toEqual([]);

    await userEvent.click(screen.getByTestId("return-to-room-leave"));
    await userEvent.click(await screen.findByTestId("conflict-leave"));
    await waitFor(() => expect(leaves).toEqual(["K7MQ2XWP"]));
  });
});
