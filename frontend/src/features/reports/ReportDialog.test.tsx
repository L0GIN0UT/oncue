import { describe, expect, it } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { setupMockBackend, signInAsUser, world } from "@/test/msw";
import { renderWithProviders } from "@/test/render";
import { ReportButton } from "./ReportDialog";

setupMockBackend();

const TARGET = "0192a000-0000-7000-8000-0000000000aa";

describe("ReportButton", () => {
  it("причины фильтруются по виду объекта", async () => {
    signInAsUser();
    renderWithProviders(<ReportButton kind="comment" targetId={TARGET} />);
    await userEvent.click(screen.getByTestId("report-open"));
    expect(await screen.findByTestId("report-reason-abuse")).toBeInTheDocument();
    expect(screen.getByTestId("report-reason-spam")).toBeInTheDocument();
    expect(screen.queryByTestId("report-reason-cheating")).toBeNull();
    expect(screen.getByRole("dialog", { name: "Жалоба на комментарий" })).toBeInTheDocument();
  });

  it("отправка: причина, текст, Idempotency-Key, контекст матча", async () => {
    signInAsUser();
    renderWithProviders(<ReportButton kind="player" targetId={TARGET} contextMatchId="match-1" />);
    await userEvent.click(screen.getByTestId("report-open"));
    const submit = await screen.findByTestId("report-submit");
    expect(submit).toBeDisabled();
    await userEvent.click(await screen.findByTestId("report-reason-cheating"));
    await userEvent.type(screen.getByTestId("report-text"), "подозрительно быстро");
    await userEvent.click(submit);
    expect(await screen.findByTestId("report-done")).toHaveTextContent("Жалоба отправлена");
    expect(world.social.reportBodies).toHaveLength(1);
    expect(world.social.reportBodies[0]).toMatchObject({
      target_kind: "player",
      target_id: TARGET,
      reason_code: "cheating",
      text: "подозрительно быстро",
      context_match_id: "match-1",
    });
    expect((world.social.reportBodies[0] as { key: string }).key).toMatch(/^[0-9a-f-]{36}$/);
  });

  it("повтор на тот же объект — «уже отправлена»", async () => {
    signInAsUser();
    world.social.reports.set(`comment:${TARGET}`, "r-1");
    renderWithProviders(<ReportButton kind="comment" targetId={TARGET} />);
    await userEvent.click(screen.getByTestId("report-open"));
    await userEvent.click(await screen.findByTestId("report-reason-spam"));
    await userEvent.click(screen.getByTestId("report-submit"));
    expect(await screen.findByTestId("report-done")).toHaveTextContent("уже отправлена");
  });

  it("счётчик текста ограничен 300 символами", async () => {
    signInAsUser();
    renderWithProviders(<ReportButton kind="collection" targetId={TARGET} />);
    await userEvent.click(screen.getByTestId("report-open"));
    const text = await screen.findByTestId("report-text");
    expect(text).toHaveAttribute("maxlength", "300");
    await userEvent.click(text);
    await userEvent.paste("а".repeat(120));
    expect(screen.getByText("120/300")).toBeInTheDocument();
  });

  it("429 лимита жалоб — понятный текст, диалог остаётся", async () => {
    signInAsUser();
    world.scenario.social = { reportRateLimited: true };
    renderWithProviders(<ReportButton kind="comment" targetId={TARGET} />);
    await userEvent.click(screen.getByTestId("report-open"));
    await userEvent.click(await screen.findByTestId("report-reason-abuse"));
    await userEvent.click(screen.getByTestId("report-submit"));
    expect(await screen.findByTestId("social-error")).toHaveTextContent("Слишком часто");
    expect(screen.getByTestId("report-dialog")).toBeInTheDocument();
  });

  it("Escape закрывает диалог, фокус возвращается на кнопку", async () => {
    signInAsUser();
    renderWithProviders(<ReportButton kind="comment" targetId={TARGET} />);
    const open = screen.getByTestId("report-open");
    await userEvent.click(open);
    await screen.findByTestId("report-dialog");
    await userEvent.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByTestId("report-dialog")).toBeNull());
    expect(open).toHaveFocus();
  });
});
