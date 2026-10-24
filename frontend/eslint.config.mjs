import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";
import { colorRestrictedSyntax } from "./eslint-color-rule.mjs";

const config = [
    ...nextVitals,
    ...nextTs,
    {
        ignores: [
            ".next/**",
            "node_modules/**",
            "src/shared/api/schema/**",
            "next-env.d.ts",
            "test-results/**",
            "playwright-report/**",
        ],
    },
    {
        rules: {
            "@typescript-eslint/no-unused-vars": ["error", { argsIgnorePattern: "^_" }],
            "@typescript-eslint/consistent-type-imports": "error",
        },
    },
    {
        // ADR 0049: цвета — только токены (src/styles). Литерал #hex/rgb()/hsl() в коде — ошибка.
        // Набор селекторов и сознательные пропуски — в eslint-color-rule.mjs (+ тест в src/styles).
        files: ["src/**/*.{ts,tsx}"],
        ignores: ["src/styles/**", "src/test/**", "src/**/*.test.{ts,tsx}"],
        rules: { "no-restricted-syntax": ["error", ...colorRestrictedSyntax] },
    },
    {
        // Фикстуры Playwright передают значение через `use` — это не React-хук.
        files: ["e2e/**/*.ts", "playwright.config.ts"],
        rules: { "react-hooks/rules-of-hooks": "off" },
    },
];

export default config;
