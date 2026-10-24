/** Строка ошибки действия social (живая область `role="alert"`). */
export function ErrorLine({ text }: { text: string | null }) {
  if (!text) return null;
  return (
    <p role="alert" className="text-sm text-miss" data-testid="social-error">
      {text}
    </p>
  );
}
