"use client";
/** Модалка дневника на маршруте `/events/[key]`: закрытие возвращает на главную. */
import { useRouter } from "next/navigation";
import { EventView } from "./EventView";

export function EventDialog({ eventKey }: { eventKey: string }) {
  const router = useRouter();
  return <EventView eventKey={eventKey} onClose={() => router.push("/")} />;
}
