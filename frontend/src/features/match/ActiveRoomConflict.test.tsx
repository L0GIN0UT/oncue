import { describe, expect, it, vi } from "vitest";
import { http, HttpResponse } from "msw";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ApiError } from "@/shared/api/errors";
import { server, setupMockBackend } from "@/test/msw";
import { renderWithProviders } from "@/test/render";
import { useActiveRoomConflict } from "./ActiveRoomConflict";

const push = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace: vi.fn(), push }) }));

setupMockBackend();

const conflict = (phase: string) =>
  new ApiError(409, {
    code: "match.active_room_conflict",
    details: { code: "K7MQ2XWP", phase },
  });

function Harness({ action, onDone }: { action: () => Promise<void>; onDone: () => void }) {
  const { guard, dialog } = useActiveRoomConflict();
  return (
    <>
      <button
        onClick={async () => {
          if ((await guard(action)) === "done") onDone();
        }}
      >
        go
      </button>
      {dialog}
    </>
  );
}

describe("диалог 409 «вы уже в комнате»", () => {
  it("выйти и продолжить: leave старой комнаты, затем тот же вход повторяется", async () => {
    let calls = 0;
    const action = vi.fn(async () => {
      calls += 1;
      if (calls === 1) throw conflict("lobby");
    });
    const leaves: string[] = [];
    server.use(
      http.post("*/api/match/v1/rooms/:code/leave", ({ params }) => {
        leaves.push(String(params.code));
        return HttpResponse.json({ version: 3, room: {} });
      }),
    );
    const onDone = vi.fn();
    renderWithProviders(<Harness action={action} onDone={onDone} />);
    await userEvent.click(screen.getByText("go"));
    const dialog = await screen.findByTestId("active-room-conflict");
    expect(dialog).toHaveTextContent("K7MQ2XWP");
    await userEvent.click(screen.getByTestId("conflict-leave"));
    await waitFor(() => expect(onDone).not.toHaveBeenCalled()); // retry идёт через guard
    await waitFor(() => expect(action).toHaveBeenCalledTimes(2));
    expect(leaves).toEqual(["K7MQ2XWP"]);
    expect(screen.queryByTestId("active-room-conflict")).not.toBeInTheDocument();
  });

  it("идущий матч — предупреждение о сдаче", async () => {
    renderWithProviders(
      <Harness
        action={async () => {
          throw conflict("level_open");
        }}
        onDone={vi.fn()}
      />,
    );
    await userEvent.click(screen.getByText("go"));
    expect(await screen.findByTestId("active-room-conflict")).toHaveTextContent("сдача");
  });

  it("«Вернуться в комнату» ведёт в старую комнату, «Отмена» закрывает", async () => {
    renderWithProviders(
      <Harness
        action={async () => {
          throw conflict("lobby");
        }}
        onDone={vi.fn()}
      />,
    );
    await userEvent.click(screen.getByText("go"));
    await userEvent.click(await screen.findByTestId("conflict-back"));
    expect(push).toHaveBeenCalledWith("/room/K7MQ2XWP");
    await userEvent.click(screen.getByTestId("conflict-cancel"));
    expect(screen.queryByTestId("active-room-conflict")).not.toBeInTheDocument();
  });

  it("ошибка выхода показывается в диалоге, действие не повторяется", async () => {
    const action = vi.fn(async () => {
      throw conflict("lobby");
    });
    server.use(
      http.post("*/api/match/v1/rooms/:code/leave", () =>
        HttpResponse.json(
          { type: "x", title: "x", status: 503, code: "match.rooms_unavailable" },
          { status: 503 },
        ),
      ),
    );
    renderWithProviders(<Harness action={action} onDone={vi.fn()} />);
    await userEvent.click(screen.getByText("go"));
    await userEvent.click(await screen.findByTestId("conflict-leave"));
    expect(await screen.findByTestId("conflict-error")).toBeInTheDocument();
    expect(action).toHaveBeenCalledTimes(1);
  });

  it("другая ошибка — не диалог, а исключение вызывающему", async () => {
    const boom = new ApiError(422, { code: "match.mode_not_online" });
    const seen: unknown[] = [];
    function Probe() {
      const { guard, dialog } = useActiveRoomConflict();
      return (
        <>
          <button
            onClick={() =>
              void guard(async () => {
                throw boom;
              }).catch((e: unknown) => seen.push(e))
            }
          >
            go
          </button>
          {dialog}
        </>
      );
    }
    renderWithProviders(<Probe />);
    await userEvent.click(screen.getByText("go"));
    await waitFor(() => expect(seen).toEqual([boom]));
    expect(screen.queryByTestId("active-room-conflict")).not.toBeInTheDocument();
  });
});

describe("диалог 409: ошибка повтора не пропадает", () => {
  it("после «Выйти и продолжить» повтор упал — ошибка остаётся в открытом диалоге", async () => {
    let calls = 0;
    const action = vi.fn(async () => {
      calls += 1;
      if (calls === 1) throw conflict("lobby");
      throw new ApiError(503, { code: "match.rooms_unavailable" });
    });
    server.use(
      http.post("*/api/match/v1/rooms/:code/leave", () =>
        HttpResponse.json({ version: 3, room: {} }),
      ),
    );
    renderWithProviders(<Harness action={action} onDone={vi.fn()} />);
    await userEvent.click(screen.getByText("go"));
    await userEvent.click(await screen.findByTestId("conflict-leave"));
    expect(await screen.findByTestId("conflict-error")).toBeInTheDocument();
    expect(screen.getByTestId("active-room-conflict")).toBeInTheDocument();
    expect(action).toHaveBeenCalledTimes(2);
  });
});
