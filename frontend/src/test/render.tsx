/** Рендер компонента со всеми провайдерами приложения (i18n `ru`, TanStack Query, тосты). */
import { useState, type ReactElement, type ReactNode } from "react";
import { render } from "@testing-library/react";
import { QueryClientProvider } from "@tanstack/react-query";
import { NextIntlClientProvider } from "next-intl";
import messages from "../../messages/ru.json";
import { makeQueryClient } from "@/shared/ui/providers";
import { ToastProvider } from "@/shared/ui/toast";
import { GameShell } from "@/features/shell/GameShell";
import { GameShellSlotsProvider, useGameShellSlots } from "@/features/shell/game-shell-slots";

export function TestProviders({ children }: { children: ReactNode }) {
  const [client] = useState(() => {
    const c = makeQueryClient();
    c.setDefaultOptions({ queries: { ...c.getDefaultOptions().queries, retry: false } });
    return c;
  });
  return (
    <NextIntlClientProvider locale="ru" messages={messages} timeZone="Europe/Moscow">
      <QueryClientProvider client={client}>
        <ToastProvider>{children}</ToastProvider>
      </QueryClientProvider>
    </NextIntlClientProvider>
  );
}

export function renderWithProviders(ui: ReactElement) {
  return render(ui, { wrapper: TestProviders });
}

/** Хост оболочки игры: рисует `GameShell` со слотами, которые экран заполняет через `GameShellSlots`. */
function GameShellHost({ children }: { children: ReactNode }) {
  return (
    <GameShellSlotsProvider>
      <ShellFromSlots />
      {children}
    </GameShellSlotsProvider>
  );
}

function ShellFromSlots() {
  return <GameShell {...useGameShellSlots()} />;
}

/** Рендер экрана игры вместе с оболочкой: подпись режима, счёт и выход видны, как на странице. */
export function renderWithGameShell(ui: ReactElement) {
  return render(<GameShellHost>{ui}</GameShellHost>, { wrapper: TestProviders });
}
