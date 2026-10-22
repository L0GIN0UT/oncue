/** Realtime комнаты (ADR 0035): канал Centrifugo, контроль версий, HTTP-tick, часы сервера. */
export { ServerClock } from "./clock";
export { DEFAULT_REALTIME_CONFIG, type RealtimeConfig } from "./config";
export * from "./message";
export { RoomLink, type LinkStatus, type RoomLinkDeps } from "./room-link";
export {
  createCentrifugeTransport,
  defaultTransportFactory,
  type RoomTransport,
  type TransportFactory,
  type TransportHandlers,
  type TransportKind,
} from "./transport";
export { VersionGate } from "./version-gate";
