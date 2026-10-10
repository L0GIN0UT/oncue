/** Общая настройка vitest: matchers jest-dom, очистка DOM и клиентов API между тестами. */
import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";
import { resetApiClients } from "@/shared/api/client";

afterEach(() => {
  cleanup();
  resetApiClients();
});

// jsdom не реализует blob:-адреса и воспроизведение медиа.
if (!URL.createObjectURL) {
  let n = 0;
  URL.createObjectURL = () => `blob:test/${++n}`;
  URL.revokeObjectURL = () => {};
}
HTMLMediaElement.prototype.play = function play() {
  return Promise.resolve();
};
HTMLMediaElement.prototype.pause = function pause() {};
