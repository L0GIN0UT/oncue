import { describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { signInAsGuest, setupMockBackend, world } from "@/test/msw";
import { renderWithProviders } from "@/test/render";
import { MODES } from "../../../e2e/mocks/world";
import { GameScreen } from "./GameScreen";

const replace = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace, push: vi.fn() }) }));

setupMockBackend();

describe("GameScreen", () => {
  it("уровень, автокомплит с клавиатуры и ревил после верного ответа", async () => {
    const user = userEvent.setup();
    const s = world.startSession(signInAsGuest(), MODES[0]!, null);
    renderWithProviders(<GameScreen sessionId={s.id} />);

    expect(await screen.findByTestId("clue-audio")).toHaveAttribute(
      "src",
      expect.stringMatching(/^blob:/),
    );
    expect(screen.getByRole("heading", { name: "Раунд 1 из 5" })).toBeInTheDocument();
    // Прибавка пропуска берётся из списка режимов — он грузится отдельно от снимка сессии.
    await waitFor(() =>
      expect(screen.getByTestId("skip")).toHaveTextContent("Пропустить (+0,5 с)"),
    );

    const input = screen.getByRole("combobox");
    await user.type(input, "Группа");
    await screen.findByRole("option", { name: /Группа крови/ });
    expect(input).toHaveAttribute("aria-expanded", "true");
    await user.keyboard("{ArrowDown}");
    expect(input).toHaveAttribute("aria-activedescendant", expect.stringContaining("opt-0"));
    await user.keyboard("{Enter}");
    expect(input).toHaveValue("Группа крови");
    await user.keyboard("{Enter}");

    const reveal = await screen.findByTestId("reveal");
    expect(reveal).toHaveTextContent("Угадано с 1-й попытки");
    expect(reveal).toHaveTextContent("+6");
    expect(screen.getByRole("heading", { name: "Группа крови" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Следующий раунд" }));
    expect(await screen.findByRole("heading", { name: "Раунд 2 из 5" })).toBeInTheDocument();
  });

  it("пропуск открывает следующий уровень и пишет попытку в историю", async () => {
    const user = userEvent.setup();
    const s = world.startSession(signInAsGuest(), MODES[0]!, null);
    renderWithProviders(<GameScreen sessionId={s.id} />);
    await user.click(await screen.findByRole("button", { name: /Пропустить/ }));
    await waitFor(() => expect(screen.getByTestId("skip")).toHaveTextContent("Пропустить (+1 с)"));
    expect(screen.getByTestId("attempts").querySelector('[data-result="skip"]')).not.toBeNull();
  });

  it("410 при выдаче ассета: раунд не засчитан, уведомление", async () => {
    world.scenario.assetGoneOnce = true;
    const s = world.startSession(signInAsGuest(), MODES[1]!, null);
    renderWithProviders(<GameScreen sessionId={s.id} />);
    expect(
      await screen.findByText("Недоступно — раунд не засчитан", { selector: "[role=status] p" }),
    ).toBeInTheDocument();
    expect(await screen.findByTestId("reveal-outcome")).toHaveTextContent(
      "Недоступно — раунд не засчитан",
    );
  });

  it("завершённая сессия сразу ведёт на итог", async () => {
    const s = world.startSession(signInAsGuest(), MODES[1]!, null);
    s.status = "finished";
    renderWithProviders(<GameScreen sessionId={s.id} />);
    await waitFor(() => expect(replace).toHaveBeenCalledWith(`/results/${s.id}`));
  });
});
