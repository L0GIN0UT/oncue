import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

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
        // Фикстуры Playwright передают значение через `use` — это не React-хук.
        files: ["e2e/**/*.ts", "playwright.config.ts"],
        rules: { "react-hooks/rules-of-hooks": "off" },
    },
];

export default config;
