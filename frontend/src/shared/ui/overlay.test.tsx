import { useState } from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { Modal } from "./modal";

function Demo() {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button onClick={() => setOpen(true)}>Открыть</button>
      <Modal open={open} onClose={() => setOpen(false)} aria-label="Окно">
        <button>Первая</button>
        <button>Вторая</button>
      </Modal>
    </>
  );
}

describe("Modal", () => {
  it("открывается с фокусом внутри, закрывается Escape и возвращает фокус", async () => {
    render(<Demo />);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Открыть" }));
    expect(screen.getByRole("dialog", { name: "Окно" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Первая" })).toHaveFocus();
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Открыть" })).toHaveFocus();
  });

  it("Tab не выходит за пределы окна", async () => {
    render(<Demo />);
    await userEvent.click(screen.getByRole("button", { name: "Открыть" }));
    await userEvent.tab();
    expect(screen.getByRole("button", { name: "Вторая" })).toHaveFocus();
    await userEvent.tab();
    expect(screen.getByRole("button", { name: "Первая" })).toHaveFocus();
    await userEvent.tab({ shift: true });
    expect(screen.getByRole("button", { name: "Вторая" })).toHaveFocus();
  });

  it("блокирует прокрутку страницы, пока открыто", async () => {
    render(<Demo />);
    await userEvent.click(screen.getByRole("button", { name: "Открыть" }));
    expect(document.body.style.overflow).toBe("hidden");
    await userEvent.keyboard("{Escape}");
    expect(document.body.style.overflow).not.toBe("hidden");
  });
});
