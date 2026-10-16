import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { server, setupMockBackend, signInAsUser, world } from "@/test/msw";
import { renderWithProviders } from "@/test/render";
import { ITEMS } from "../../../../e2e/mocks/world";
import { CollectionEditor } from "./CollectionEditor";

const push = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace: vi.fn(), push }) }));

setupMockBackend();

const films = ITEMS.filter((i) => i.domain === "film");

/** Черновик игрока в мок-мире: `n` фильмов, режим кадров. */
function playerDraft(n = 5, mode: string | null = "film.collection.frame") {
  const subject = signInAsUser();
  const c = world.ugc.create(subject, "film", "Мой сборник");
  c.draft!.body = {
    ...c.draft!.body,
    default_mode_key: mode,
    items: films.slice(0, n).map((f) => ({ media_item_id: f.id, custom: null })),
  };
  return c;
}

/** PUT черновика за тест (base_revision каждого). */
function recordSaves(): number[] {
  const bases: number[] = [];
  server.events.on("request:start", async ({ request }) => {
    if (request.method === "PUT" && request.url.endsWith("/draft"))
      bases.push(((await request.clone().json()) as { base_revision: number }).base_revision);
  });
  return bases;
}

describe("CollectionEditor", () => {
  beforeEach(() => {
    push.mockClear();
    server.events.removeAllListeners();
    localStorage.clear();
  });
  afterEach(() => vi.restoreAllMocks());

  it("автосохранение после паузы: PUT поверх текущей ревизии, ревизия растёт", async () => {
    const user = userEvent.setup();
    const c = playerDraft();
    const saves = recordSaves();
    renderWithProviders(<CollectionEditor draft={world.ugc.draftOut(c)} debounceMs={30} />);
    await user.type(screen.getByTestId("editor-title"), "!");
    await waitFor(() => expect(c.draft!.body.title).toBe("Мой сборник!"));
    expect(saves).toEqual([1]);
    expect(screen.getByTestId("editor-save-status")).toHaveTextContent("Черновик сохранён");
    // Следующая правка уходит уже поверх новой ревизии.
    await user.type(screen.getByTestId("editor-title"), "?");
    await waitFor(() => expect(c.draft!.body.title).toBe("Мой сборник!?"));
    expect(saves).toEqual([1, 2]);
  });

  it("409 → экран конфликта → «моя версия» сохраняет поверх актуальной ревизии", async () => {
    const user = userEvent.setup();
    const c = playerDraft();
    world.ugc.scenario.draftConflictOnce = true;
    const saves = recordSaves();
    renderWithProviders(<CollectionEditor draft={world.ugc.draftOut(c)} debounceMs={30} />);
    await user.type(screen.getByTestId("editor-title"), "!");
    const conflict = await screen.findByTestId("draft-conflict");
    expect(conflict).toHaveTextContent("Safari · iOS");
    expect(conflict).toHaveTextContent("Мой сборник (планшет)");
    const theirs = c.draft!.revision;

    await user.click(within(conflict).getByTestId("draft-keep-mine"));
    await waitFor(() => expect(screen.queryByTestId("draft-conflict")).not.toBeInTheDocument());
    expect(saves.at(-1)).toBe(theirs);
    expect(c.draft!.body.title).toBe("Мой сборник!");
  });

  it("«с другого устройства»: тело сервера, своих правок нет, автосохранение не идёт", async () => {
    const user = userEvent.setup();
    const c = playerDraft();
    world.ugc.scenario.draftConflictOnce = true;
    const saves = recordSaves();
    renderWithProviders(<CollectionEditor draft={world.ugc.draftOut(c)} debounceMs={30} />);
    await user.type(screen.getByTestId("editor-title"), "!");
    await user.click(await screen.findByTestId("draft-take-theirs"));
    expect(screen.getByTestId("editor-title")).toHaveValue("Мой сборник (планшет)");
    await new Promise((r) => setTimeout(r, 80));
    expect(saves).toHaveLength(1);
    expect(screen.getByTestId("editor-save-status")).toHaveTextContent("Черновик сохранён");
  });

  it("публикация с ошибками: тексты по кодам и полям (единицы, режим, эмодзи)", async () => {
    const user = userEvent.setup();
    const c = playerDraft(2, null);
    c.draft!.body.items![0]!.custom = { emoji: "🚀🌌" };
    renderWithProviders(<CollectionEditor draft={world.ugc.draftOut(c)} debounceMs={30} />);
    await user.click(screen.getByTestId("editor-publish"));
    const errors = await screen.findByTestId("editor-errors");
    expect(errors).toHaveTextContent("Слишком мало единиц: нужно не меньше 5");
    expect(errors).toHaveTextContent("Выберите режим сборника");
    expect(errors).toHaveTextContent("Свои эмодзи: нужно не меньше 6");
    // Ошибка эмодзи — и в строке единицы.
    const row = screen.getByTestId(`editor-item-${films[0]!.id}`);
    expect(within(row).getAllByText("Свои эмодзи: нужно не меньше 6").length).toBeGreaterThan(0);
    expect(push).not.toHaveBeenCalled();
  });

  it("публикация без ошибок ведёт на страницу сборника; эмодзи проверяются на клиенте", async () => {
    const user = userEvent.setup();
    const c = playerDraft(5);
    renderWithProviders(<CollectionEditor draft={world.ugc.draftOut(c)} debounceMs={30} />);
    const emoji = screen.getByTestId(`editor-emoji-${films[0]!.id}`);
    await user.type(emoji, "abc");
    expect(screen.getByTestId(`editor-emoji-error-${films[0]!.id}`)).toHaveTextContent(
      "Только эмодзи",
    );
    await user.clear(emoji);
    await user.type(emoji, "🚀🌌⏳🌽🪐📚");
    expect(screen.queryByTestId(`editor-emoji-error-${films[0]!.id}`)).not.toBeInTheDocument();
    await user.click(screen.getByTestId("editor-publish"));
    await waitFor(() => expect(push).toHaveBeenCalledWith(`/collections/${c.id}`));
    expect(c.status).toBe("published");
    expect(c.items[0]!.emoji).toBe("🚀🌌⏳🌽🪐📚");
  });

  it("играбельность в режиме — отметка у каждой единицы", async () => {
    const c = playerDraft(5);
    renderWithProviders(<CollectionEditor draft={world.ugc.draftOut(c)} debounceMs={30} />);
    await waitFor(() =>
      expect(screen.getByTestId(`editor-playable-${films[0]!.id}`)).toHaveAttribute(
        "data-playable",
        "true",
      ),
    );
  });

  it("порядок с клавиатуры: стрелка на ручке перемещает единицу", async () => {
    const user = userEvent.setup();
    const c = playerDraft(3);
    renderWithProviders(<CollectionEditor draft={world.ugc.draftOut(c)} debounceMs={30} />);
    const handles = screen.getAllByRole("button", { name: /Переместить/ });
    handles[0]!.focus();
    await user.keyboard("{ArrowDown}");
    const rows = within(screen.getByTestId("editor-items")).getAllByRole("listitem");
    expect(rows[0]).toHaveAttribute("data-testid", `editor-item-${films[1]!.id}`);
    expect(rows[1]).toHaveAttribute("data-testid", `editor-item-${films[0]!.id}`);
  });

  it("без доступа к localStorage конструктор работает", async () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("quota", "QuotaExceededError");
    });
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new DOMException("denied", "SecurityError");
    });
    const user = userEvent.setup();
    const c = playerDraft();
    renderWithProviders(<CollectionEditor draft={world.ugc.draftOut(c)} debounceMs={30} />);
    await user.type(screen.getByTestId("editor-title"), "!");
    await waitFor(() => expect(c.draft!.body.title).toBe("Мой сборник!"));
  });

  it("локальная копия старой ревизии при загрузке — экран конфликта", async () => {
    const c = playerDraft();
    localStorage.setItem(
      `gv:collection-draft:${c.id}`,
      JSON.stringify({ body: { ...c.draft!.body, title: "Офлайн" }, baseRevision: 0 }),
    );
    renderWithProviders(<CollectionEditor draft={world.ugc.draftOut(c)} debounceMs={30} />);
    expect(await screen.findByTestId("draft-conflict")).toBeInTheDocument();
    expect(screen.getByTestId("editor-title")).toHaveValue("Офлайн");
  });
});
