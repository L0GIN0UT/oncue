import { describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { server, setupMockBackend, signInAsUser, world } from "@/test/msw";
import { renderWithProviders } from "@/test/render";
import type { AvatarCard } from "@/shared/api/types";
import { AvatarBlock } from "./AvatarBlock";

setupMockBackend();

const entitled: AvatarCard = {
  entitled: true,
  formats: ["jpeg", "png", "webp"],
  max_bytes: 1_000,
  request: null,
};

const PNG_HEAD = new Uint8Array([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0, 0, 0, 13]);
const png = (size = 100, name = "a.png", type = "image/png") => {
  const f = new File([PNG_HEAD], name, { type });
  Object.defineProperty(f, "size", { value: size });
  return f;
};

const upload = (file: File) => userEvent.upload(screen.getByTestId("avatar-upload"), file);

describe("AvatarBlock: статус", () => {
  it.each([
    [{ ...entitled }, null, "none"],
    [{ ...entitled }, "/media/a.webp", "current"],
    [
      {
        ...entitled,
        request: { status: "pending" as const, submitted_at: "2026-10-23T10:00:00Z" },
      },
      "/media/a.webp",
      "pending",
    ],
    [
      {
        ...entitled,
        request: { status: "approved" as const, submitted_at: "2026-10-23T10:00:00Z" },
      },
      "/media/a.webp",
      "approved",
    ],
  ])("%#", (avatar, url, status) => {
    renderWithProviders(<AvatarBlock avatar={avatar} currentUrl={url} />);
    expect(screen.getByTestId("avatar-status")).toHaveAttribute("data-status", status);
  });

  it("отклонена с известной причиной — ru-подпись причины", () => {
    renderWithProviders(
      <AvatarBlock
        avatar={{
          ...entitled,
          request: {
            status: "rejected",
            submitted_at: "2026-10-23T10:00:00Z",
            reason_code: "copyright",
          },
        }}
      />,
    );
    const status = screen.getByTestId("avatar-status");
    expect(status).toHaveAttribute("data-status", "rejected");
    expect(status).toHaveTextContent("Нарушение авторских прав");
  });

  it("отклонена с неизвестной причиной — общий текст, код не показывается", () => {
    renderWithProviders(
      <AvatarBlock
        avatar={{
          ...entitled,
          request: {
            status: "rejected",
            submitted_at: "2026-10-23T10:00:00Z",
            reason_code: "secret_code_42",
          },
        }}
      />,
    );
    const status = screen.getByTestId("avatar-status");
    expect(status).toHaveTextContent("не прошла модерацию");
    expect(status).not.toHaveTextContent("secret_code_42");
  });

  it("без права — ссылка в магазин и нет загрузки", () => {
    renderWithProviders(<AvatarBlock avatar={{ ...entitled, entitled: false }} />);
    expect(screen.getByRole("link", { name: "Перейти в магазин" })).toHaveAttribute(
      "href",
      "/shop",
    );
    expect(screen.queryByTestId("avatar-upload")).toBeNull();
  });
});

describe("AvatarBlock: загрузка", () => {
  it("превью, отправка multipart с Idempotency-Key и сообщение об успехе", async () => {
    signInAsUser();
    const seen: { key: string | null; type: string | null } = { key: null, type: null };
    server.use(
      http.post("*/api/progression/v1/me/avatar", async ({ request }) => {
        seen.key = request.headers.get("idempotency-key");
        seen.type = request.headers.get("content-type");
        return HttpResponse.json(
          {
            asset_id: "a",
            status: "pending",
            reason_code: null,
            submitted_at: "2026-10-24T10:00:00Z",
          },
          { status: 202 },
        );
      }),
    );
    renderWithProviders(<AvatarBlock avatar={entitled} />);
    await upload(png());
    expect(screen.getByRole("img", { name: "Предпросмотр" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Отправить на проверку" }));
    expect(await screen.findByTestId("avatar-sent")).toBeInTheDocument();
    expect(seen.key).toBeTruthy();
    expect(seen.type).toMatch(/^multipart\/form-data; boundary=/);
  });

  it("слишком большой файл и чужой формат отклоняются до отправки", async () => {
    signInAsUser();
    const spy = vi.fn();
    server.events.on("request:start", spy);
    renderWithProviders(<AvatarBlock avatar={entitled} />);
    await userEvent.upload(screen.getByTestId("avatar-upload"), png(5_000), {});
    expect(await screen.findByRole("alert")).toHaveTextContent("больше допустимого размера");
    expect(screen.queryByRole("button", { name: "Отправить на проверку" })).toBeNull();

    await userEvent.upload(
      screen.getByTestId("avatar-upload"),
      new File(["GIF89a......"], "a.gif", { type: "image/gif" }),
      {
        applyAccept: false,
      },
    );
    expect(await screen.findByRole("alert")).toHaveTextContent("формат не подходит");
    expect(spy.mock.calls.filter(([e]) => e.request.url.includes("/me/avatar"))).toHaveLength(0);
  });

  it.each([
    ["too_large" as const, "слишком большой"],
    ["invalid" as const, "Не удалось прочитать картинку"],
    ["rate" as const, "Слишком много попыток"],
  ])("ответ сервера %s — сообщение", async (fail, text) => {
    signInAsUser();
    world.scenario = { avatarFail: fail };
    renderWithProviders(<AvatarBlock avatar={entitled} />);
    await upload(png());
    await userEvent.click(screen.getByRole("button", { name: "Отправить на проверку" }));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent(text));
  });
});
