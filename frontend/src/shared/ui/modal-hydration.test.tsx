import { screen } from "@testing-library/react";
import { act } from "react";
import { hydrateRoot } from "react-dom/client";
import { renderToString } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Modal } from "./modal";

const view = (
  <Modal open onClose={() => undefined} aria-label="Окно">
    <button>Внутри</button>
  </Modal>
);

describe("Modal — гидратация", () => {
  afterEach(() => {
    document.body.innerHTML = "";
  });

  it("открытая с первого кадра (прямая ссылка) не расходится с серверной разметкой", async () => {
    // Сервер порталов не рисует: в jsdom `document` есть, поэтому html берём как у сервера — пустым.
    const server = renderToString(<div id="root" />);
    const container = document.createElement("div");
    container.innerHTML = server;
    document.body.append(container);

    const errors: unknown[] = [];
    const log = vi.spyOn(console, "error").mockImplementation((...a) => void errors.push(a));
    await act(async () => {
      hydrateRoot(container.firstElementChild!, view, {
        onRecoverableError: (e) => errors.push(e),
      });
    });
    log.mockRestore();

    expect(errors).toEqual([]);
    expect(document.querySelector('[role="dialog"]')).not.toBeNull();
    // Поведение оверлея включилось уже после гидратации: фокус внутри окна.
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Внутри" }));
  });
});
