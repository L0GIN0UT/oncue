import { describe, expect, it } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { server, setupMockBackend, signInAsGuest, signInAsUser, world } from "@/test/msw";
import { renderWithProviders } from "@/test/render";
import { commentsBlock, seedComments } from "../../../e2e/mocks/social";
import { CommentsSection } from "./CommentsSection";

setupMockBackend();

const COL = "0192a000-0000-7000-8000-0000000000c1";
const ME = "00000000-0000-4000-8000-000000000901";

/** Моё сообщение: ждём, пока подтянется мой user_id (identity `/me`). */
async function findMine(): Promise<HTMLElement> {
  await waitFor(() =>
    expect(
      screen.getAllByTestId("comment").some((c) => c.getAttribute("data-mine") === "true"),
    ).toBe(true),
  );
  return screen.getAllByTestId("comment").find((c) => c.getAttribute("data-mine") === "true")!;
}

function open(subject: string | null = `user.${ME}`) {
  server.use(http.get("*/api/identity/v1/me", () => HttpResponse.json({ id: ME })));
  seedComments(world, COL, 5);
  const block = commentsBlock(world, COL, subject);
  return renderWithProviders(<CommentsSection collectionId={COL} block={block} />);
}

describe("CommentsSection", () => {
  it("первая страница из блока bff, «показать ещё» догружает по курсору", async () => {
    signInAsUser();
    open();
    expect(screen.getAllByTestId("comment")).toHaveLength(2);
    expect(screen.getByTestId("comments-total")).toHaveTextContent("5 комментариев");
    await userEvent.click(screen.getByTestId("comments-more"));
    await waitFor(() => expect(screen.getAllByTestId("comment")).toHaveLength(4));
    await userEvent.click(screen.getByTestId("comments-more"));
    await waitFor(() => expect(screen.getAllByTestId("comment")).toHaveLength(5));
    expect(screen.queryByTestId("comments-more")).toBeNull();
  });

  it("публикация: счётчик, ключ идемпотентности, комментарий сверху, поле очищено", async () => {
    signInAsUser();
    open();
    const field = await screen.findByTestId("comment-text");
    expect(field).toHaveAttribute("maxlength", "500");
    await userEvent.type(field, "Отличный сборник");
    expect(screen.getByTestId("comment-counter")).toHaveTextContent("16/500");
    await userEvent.click(screen.getByTestId("comment-submit"));
    await waitFor(() => expect(screen.getAllByTestId("comment")).toHaveLength(3));
    expect(screen.getAllByTestId("comment-body")[0]).toHaveTextContent("Отличный сборник");
    expect(field).toHaveValue("");
    expect(screen.getByTestId("comments-total")).toHaveTextContent("6 комментариев");
    expect(world.social.idempotency.size).toBe(1);
  });

  it.each([
    ["blocked", "блокировки"],
    ["chat_muted", "временно нельзя писать"],
    ["comment_rejected", "не прошёл проверку"],
    ["comments_rate_limit", "Слишком много комментариев"],
  ] as const)("ошибка публикации %s — своя подсказка, текст не теряется", async (code, text) => {
    signInAsUser();
    world.scenario.social = { commentError: code };
    open();
    await userEvent.type(await screen.findByTestId("comment-text"), "Привет");
    await userEvent.click(screen.getByTestId("comment-submit"));
    expect(await screen.findByTestId("social-error")).toHaveTextContent(text);
    expect(await screen.findByTestId("comment-text")).toHaveValue("Привет");
  });

  it("свой комментарий можно править и удалять; чужой — только жалоба", async () => {
    signInAsUser();
    open();
    await userEvent.type(await screen.findByTestId("comment-text"), "Мой");
    await userEvent.click(screen.getByTestId("comment-submit"));
    const mine = await findMine();
    await userEvent.click(within(mine).getByTestId("comment-edit"));
    const edit = within(mine).getByTestId("comment-edit-text");
    await userEvent.clear(edit);
    await userEvent.type(edit, "Правка");
    await userEvent.click(within(mine).getByRole("button", { name: "Сохранить" }));
    await waitFor(() =>
      expect(within(mine).getByTestId("comment-body")).toHaveTextContent("Правка"),
    );
    expect(within(mine).getByText("изменён")).toBeInTheDocument();

    const other = screen
      .getAllByTestId("comment")
      .find((c) => c.getAttribute("data-mine") === "false")!;
    expect(within(other).queryByTestId("comment-edit")).toBeNull();
    expect(within(other).queryByTestId("comment-delete")).toBeNull();
    expect(within(other).getByTestId("comment-report")).toBeInTheDocument();

    await userEvent.click(within(mine).getByTestId("comment-delete"));
    await waitFor(() => expect(screen.getAllByTestId("comment")).toHaveLength(2));
  });

  it("истёкшее окно правки — 409 с пояснением", async () => {
    signInAsUser();
    open();
    await userEvent.type(await screen.findByTestId("comment-text"), "Старый");
    await userEvent.click(screen.getByTestId("comment-submit"));
    const mine = await findMine();
    const posted = world.social.commentsOf(COL).find((c) => c.author_id === ME)!;
    posted.created_at = "2026-01-01T00:00:00Z";
    await userEvent.click(within(mine).getByTestId("comment-edit"));
    await userEvent.type(within(mine).getByTestId("comment-edit-text"), "!");
    await userEvent.click(within(mine).getByRole("button", { name: "Сохранить" }));
    expect(await within(mine).findByTestId("social-error")).toHaveTextContent("Время правки вышло");
  });

  it("гость читает, но вместо формы — приглашение войти", async () => {
    signInAsGuest();
    open(null);
    expect(screen.getByTestId("comments-login")).toHaveTextContent("Войдите");
    expect(screen.queryByTestId("comment-text")).toBeNull();
    expect(screen.queryByTestId("comment-report")).toBeNull();
    expect(screen.getAllByTestId("comment")).toHaveLength(2);
  });

  it("блок недоступен — заглушка; блока нет — секции нет", async () => {
    signInAsUser();
    const { unmount } = renderWithProviders(
      <CommentsSection collectionId={COL} block={{ status: "unavailable", data: null }} />,
    );
    expect(screen.getByTestId("comments-unavailable")).toBeInTheDocument();
    unmount();
    renderWithProviders(<CommentsSection collectionId={COL} block={null} />);
    expect(screen.queryByTestId("comments")).toBeNull();
  });
});
