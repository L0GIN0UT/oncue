import { describe, expect, it } from "vitest";
import { ServerClock } from "./clock";

describe("ServerClock", () => {
  it("без отметок сервера — локальное время", () => {
    const clock = new ServerClock(() => 1_000);
    expect(clock.now()).toBe(1_000);
    expect(clock.offset()).toBe(0);
  });

  it("смещение по server_now; по запросу — по середине RTT", () => {
    let local = 10_000;
    const clock = new ServerClock(() => local);
    // Запрос ушёл в 9_800, ответ в 10_000: середина 9_900, сервер 15_000 → смещение 5_100.
    clock.observe(new Date(15_000).toISOString(), 9_800);
    expect(clock.offset()).toBe(5_100);
    local = 11_000;
    expect(clock.now()).toBe(16_100);
  });

  it("таймер фазы считается по часам сервера, не меньше нуля", () => {
    const clock = new ServerClock(() => 0);
    clock.observe(new Date(60_000).toISOString());
    expect(clock.remainingMs(new Date(85_000).toISOString())).toBe(25_000);
    expect(clock.remainingMs(new Date(50_000).toISOString())).toBe(0);
    expect(clock.remainingMs(null)).toBeNull();
    expect(clock.remainingMs("не дата")).toBeNull();
  });

  it("мелкий дребезг сглаживается, скачок часов принимается сразу", () => {
    const clock = new ServerClock(() => 0);
    clock.observe(new Date(1_000).toISOString());
    clock.observe(new Date(1_500).toISOString());
    expect(clock.offset()).toBe(1_100);
    clock.observe(new Date(90_000).toISOString());
    expect(clock.offset()).toBe(90_000);
  });
});
