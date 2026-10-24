/**
 * Правило ADR 0049 «в коде нет литералов цвета» — настройки `no-restricted-syntax`.
 * Вынесено из eslint.config.mjs, чтобы тот же набор проверял `src/styles/eslint-color-rule.test.ts`.
 *
 * Ловит: #hex (3/4/6/8 цифр) и функции цвета rgb()/rgba()/hsl()/hsla()/oklch()/oklab()/hwb()
 * в строках и шаблонах. НЕ ловит сознательно (зафиксировано тестом): именованные цвета
 * (`red`), `transparent`/`currentColor`, `color-mix()`, `var(--…)` — они либо не задают
 * значение цвета, либо собираются из токенов. Не считаются цветом якоря: строка в атрибуте
 * `href` и аргумент `querySelector`/`closest`/`matches` (`#add`, `#bad`, `#face` — это id).
 */

const HEX = "#([0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{3,4})($|[^0-9A-Za-z_-])";
const FUNC = "\\b(rgba?|hsla?|oklch|oklab|hwb)\\(";

const NOT_ANCHOR =
    ":not(JSXAttribute[name.name='href'] > Literal)" +
    ":not(CallExpression[callee.property.name=/^(querySelector|querySelectorAll|closest|matches)$/] > Literal)";

const message =
    "Литерал цвета запрещён (ADR 0049): используйте токен (var(--color-*) или класс Tailwind) из src/styles.";

export const colorRestrictedSyntax = [
    { selector: `Literal[raw=/${HEX}/]${NOT_ANCHOR}`, message },
    { selector: `TemplateElement[value.raw=/${HEX}/]`, message },
    { selector: `Literal[raw=/${FUNC}/i]`, message },
    { selector: `TemplateElement[value.raw=/${FUNC}/i]`, message },
];
