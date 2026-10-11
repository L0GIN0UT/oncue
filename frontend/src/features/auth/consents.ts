/**
 * Обязательные согласия регистрации. Источник — та же настройка, что у identity
 * (`GV_CONSENTS__REQUIRED`, формат `kind:version,...`), читается на сервере Next.
 */
export type ConsentRequirement = { kind: string; version: string };

export const DEFAULT_CONSENTS = "terms:2026-10,privacy:2026-10,age_18:1";

export function parseConsents(raw: string | undefined): ConsentRequirement[] {
  return (raw ?? DEFAULT_CONSENTS)
    .split(",")
    .map((part) => part.trim())
    .filter(Boolean)
    .map((part) => {
      const [kind = "", version = ""] = part.split(":").map((s) => s.trim());
      return { kind, version };
    })
    .filter((c) => c.kind && c.version);
}
