"use client";
/** Клиентские провайдеры: TanStack Query (игровые данные — `no-store`, без фоновых повторов на фокус). */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";
import { isApiError } from "@/shared/api/errors";
import { ToastProvider } from "./toast";

export function makeQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 0,
        refetchOnWindowFocus: false,
        // Ошибки 4xx — ответ сервера, повтор не поможет; сеть и 5xx — один повтор.
        retry: (count, error) =>
          count < 1 && !(isApiError(error) && error.status >= 400 && error.status < 500),
      },
      mutations: { retry: false },
    },
  });
}

export function Providers({ children }: { children: ReactNode }) {
  const [client] = useState(makeQueryClient);
  return (
    <QueryClientProvider client={client}>
      <ToastProvider>{children}</ToastProvider>
    </QueryClientProvider>
  );
}
