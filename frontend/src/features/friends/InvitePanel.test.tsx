import { describe, expect, it } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { server, setupMockBackend, signInAsUser, world } from "@/test/msw";
import { renderWithProviders, TestProviders } from "@/test/render";
import { NetworkError } from "@/shared/api/errors";
import { InvitePanel } from "./InvitePanel";
import { useSocialAction } from "./use-action";

setupMockBackend();

describe("InvitePanel", () => {
  it("показывает только друзей онлайн; приглашение уходит с кодом комнаты и ключом", async () => {
    signInAsUser();
    renderWithProviders(<InvitePanel roomCode="ABCD2345" />);
    const row = await screen.findByTestId("invite-Анна");
    expect(screen.queryByTestId("invite-Борис")).toBeNull();
    expect(screen.queryByTestId("invite-Клара")).toBeNull();
    await userEvent.click(within(row).getByTestId("invite-send"));
    expect(await within(row).findByTestId("invite-sent")).toBeInTheDocument();
    expect(world.social.invites).toHaveLength(1);
    expect(world.social.invites[0]).toMatchObject({ room_code: "ABCD2345" });
    expect(world.social.invites[0]!.key).toMatch(/^[0-9a-f-]{36}$/);
  });

  it("403 social.invite_forbidden — понятный текст, кнопка остаётся", async () => {
    signInAsUser();
    world.scenario.social = { inviteForbidden: true };
    renderWithProviders(<InvitePanel roomCode="ABCD2345" />);
    const row = await screen.findByTestId("invite-Анна");
    await userEvent.click(within(row).getByTestId("invite-send"));
    expect(await within(row).findByRole("alert")).toHaveTextContent(
      "Этого игрока сейчас нельзя пригласить",
    );
    expect(within(row).getByTestId("invite-send")).toBeEnabled();
  });

  it("429 — «слишком часто»", async () => {
    signInAsUser();
    world.scenario.social = { inviteRateLimited: true };
    renderWithProviders(<InvitePanel roomCode="ABCD2345" />);
    const row = await screen.findByTestId("invite-Анна");
    await userEvent.click(within(row).getByTestId("invite-send"));
    expect(await within(row).findByRole("alert")).toHaveTextContent("Слишком часто");
  });

  it("нет друзей онлайн — пояснение", async () => {
    signInAsUser();
    world.social.friends = world.social.friends.map((f) => ({ ...f, status: "offline" as const }));
    renderWithProviders(<InvitePanel roomCode="ABCD2345" />);
    expect(await screen.findByTestId("invite-empty")).toBeInTheDocument();
  });

  it("сбой списка друзей не ломает лобби", async () => {
    signInAsUser();
    server.use(
      http.get("*/api/social/v1/me/friends", () =>
        HttpResponse.json({ code: "social.unavailable", title: "x", status: 503 }, { status: 503 }),
      ),
    );
    renderWithProviders(<InvitePanel roomCode="ABCD2345" />);
    expect(await screen.findByText("Список друзей сейчас недоступен.")).toBeInTheDocument();
  });
});

describe("useSocialAction: ключ идемпотентности", () => {
  it("после сетевого сбоя повтор идёт с тем же ключом, после ответа сервера — с новым", async () => {
    const { result } = renderHook(() => useSocialAction(), { wrapper: TestProviders });
    const seen: string[] = [];
    const fail = async (key: string) => {
      seen.push(key);
      throw new NetworkError();
    };
    await act(async () => void (await result.current.run("scope", fail)));
    await act(async () => void (await result.current.run("scope", fail)));
    expect(seen[0]).toBe(seen[1]);

    const ok = async (key: string) => void seen.push(key);
    await act(async () => void (await result.current.run("scope", ok)));
    expect(seen[2]).toBe(seen[0]);
    await act(async () => void (await result.current.run("scope", ok)));
    expect(seen[3]).not.toBe(seen[0]);
  });

  it("ответ-ошибка сервера завершает намерение: следующий ключ новый", async () => {
    const { result } = renderHook(() => useSocialAction(), { wrapper: TestProviders });
    const seen: string[] = [];
    const { ApiError } = await import("@/shared/api/errors");
    const reject = async (key: string) => {
      seen.push(key);
      throw new ApiError(403, { code: "social.blocked", title: "x", status: 403 });
    };
    await act(async () => void (await result.current.run("s", reject)));
    await act(async () => void (await result.current.run("s", reject)));
    expect(seen[0]).not.toBe(seen[1]);
    expect(result.current.error).toContain("блокировки");
  });
});
