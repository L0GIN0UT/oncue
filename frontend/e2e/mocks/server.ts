/**
 * HTTP-сервер мок-бэкенда для Playwright: `tsx e2e/mocks/server.ts` (порт `MOCK_PORT`, 3101).
 *
 * Next в e2e переписывает `/api/*` и `/media/*` сюда (`GV_DEV_API_ORIGIN`, см. src/proxy.ts),
 * а SSR ходит в bff по `GV_BFF_INTERNAL_URL` — пути `/v1/...` сервер считает путями bff.
 * Управление сценариями: `POST /__mock/reset`, `POST /__mock/scenario {desyncOnce: true}`;
 * сборники и медиатека (этап 3b) — `POST /__mock/scenario {ugc: {draftConflictOnce: true}}`;
 * онлайн (этап 4a) — `POST /__mock/scenario {match: {dropNextMessage: true}}` (см. match.ts).
 */
import { createServer, type IncomingMessage } from "node:http";
import { getResponse } from "msw";
import { createHandlers } from "./handlers";
import { MockWorld, type Scenario } from "./world";
import type { UgcScenario } from "./ugc";
import { MatchWorld, type MatchScenario } from "./match";

const port = Number(process.env.MOCK_PORT ?? 3101);
const world = new MockWorld();
const matchWorld = new MatchWorld(world);
matchWorld.startClock();
const handlers = createHandlers(world, matchWorld);

async function readBody(req: IncomingMessage): Promise<Buffer> {
  const chunks: Buffer[] = [];
  for await (const chunk of req) chunks.push(chunk as Buffer);
  return Buffer.concat(chunks);
}

const server = createServer(async (req, res) => {
  try {
    const body = await readBody(req);
    let path = req.url ?? "/";
    if (path === "/__mock/reset" && req.method === "POST") {
      world.reset();
      matchWorld.reset();
      res.writeHead(204).end();
      return;
    }
    if (path === "/__mock/scenario" && req.method === "POST") {
      const { ugc, match, ...rest } = JSON.parse(body.toString() || "{}") as Scenario & {
        ugc?: UgcScenario;
        match?: MatchScenario;
      };
      world.scenario = { ...world.scenario, ...rest };
      if (ugc) world.ugc.scenario = { ...world.ugc.scenario, ...ugc };
      if (match) matchWorld.scenario = { ...matchWorld.scenario, ...match };
      res.writeHead(204).end();
      return;
    }
    if (path === "/healthz") {
      res.writeHead(200).end("ok");
      return;
    }
    // Внутренний адрес bff (SSR): /v1/... → /api/web/v1/...
    if (path.startsWith("/v1/")) path = `/api/web${path}`;
    const headers = new Headers();
    for (const [k, v] of Object.entries(req.headers)) {
      if (typeof v === "string") headers.set(k, v);
      else if (Array.isArray(v)) headers.set(k, v.join(", "));
    }
    const request = new Request(`http://${req.headers.host ?? "localhost"}${path}`, {
      method: req.method,
      headers,
      body: req.method === "GET" || req.method === "HEAD" ? undefined : new Uint8Array(body),
    });
    const response = await getResponse(handlers, request);
    if (!response) {
      res.writeHead(404, { "Content-Type": "application/problem+json" });
      res.end(
        JSON.stringify({
          type: "about:blank",
          title: "Not found",
          status: 404,
          code: "route.not_found",
        }),
      );
      return;
    }
    const out: Record<string, string | string[]> = {};
    response.headers.forEach((value, key) => {
      if (key !== "set-cookie") out[key] = value;
    });
    const cookies = response.headers.getSetCookie();
    if (cookies.length) out["set-cookie"] = cookies;
    res.writeHead(response.status, out);
    res.end(Buffer.from(await response.arrayBuffer()));
  } catch (e) {
    console.error(e);
    res.writeHead(500).end();
  }
});

server.listen(port, "127.0.0.1", () => console.log(`mock API on http://127.0.0.1:${port}`));
process.on("SIGTERM", () => server.close(() => process.exit(0)));
