// Генерация типов API из контрактов OpenAPI (contracts/openapi/*.json).
// `pnpm gen:api` — перезаписать src/shared/api/schema/*; `pnpm gen:api:check` — сверить (для CI).
// Сгенерированные файлы руками не правятся.
import { readFile, writeFile, mkdir } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import openapiTS, { astToString } from "openapi-typescript";

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, "..");
const contractsDir = path.resolve(root, "..", "contracts", "openapi");
const outDir = path.join(root, "src", "shared", "api", "schema");
const SERVICES = ["game", "bff_web", "catalog", "identity", "collections", "library"];
const check = process.argv.includes("--check");

const HEADER = (name) =>
    `/* eslint-disable */\n// Сгенерировано \`pnpm gen:api\` из contracts/openapi/${name}.json. Не править руками.\n\n`;

let stale = [];
await mkdir(outDir, { recursive: true });
for (const name of SERVICES) {
    const source = pathToFileURL(path.join(contractsDir, `${name}.json`));
    const ast = await openapiTS(source, { alphabetize: true });
    const code = HEADER(name) + astToString(ast);
    const target = path.join(outDir, `${name}.ts`);
    if (check) {
        const current = await readFile(target, "utf8").catch(() => "");
        if (current !== code) stale.push(path.relative(root, target));
    } else {
        await writeFile(target, code);
    }
}
if (check && stale.length) {
    console.error(`Типы API устарели, выполните pnpm gen:api: ${stale.join(", ")}`);
    process.exit(1);
}
console.log(
    check
        ? "Типы API совпадают с контрактами"
        : `Типы API записаны в ${path.relative(root, outDir)}`,
);
