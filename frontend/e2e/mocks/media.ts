/** Синтетические ассеты для моков: WAV-тон нужной длины и SVG-«кадр» с размытием уровня. */

/** Моно WAV 8 кГц, 8 бит: тон, высота которого зависит от `seed` (разные треки звучат по-разному). */
export function audioWav(seconds: number, seed: number): ArrayBuffer {
  const rate = 8000;
  const samples = Math.max(1, Math.round(rate * seconds));
  const buf = new ArrayBuffer(44 + samples);
  const v = new DataView(buf);
  const str = (o: number, s: string) =>
    [...s].forEach((c, i) => v.setUint8(o + i, c.charCodeAt(0)));
  str(0, "RIFF");
  v.setUint32(4, 36 + samples, true);
  str(8, "WAVE");
  str(12, "fmt ");
  v.setUint32(16, 16, true);
  v.setUint16(20, 1, true);
  v.setUint16(22, 1, true);
  v.setUint32(24, rate, true);
  v.setUint32(28, rate, true);
  v.setUint16(32, 1, true);
  v.setUint16(34, 8, true);
  str(36, "data");
  v.setUint32(40, samples, true);
  const freq = 220 + 55 * (seed % 8);
  for (let i = 0; i < samples; i++) {
    v.setUint8(44 + i, 128 + Math.round(40 * Math.sin((2 * Math.PI * freq * i) / rate)));
  }
  return buf;
}

/** SVG-кадр: цветные фигуры, размытие `blur` пикселей (0 — чёткий кадр ревила). */
export function frameSvg(blur: number, seed: number): string {
  const hue = (seed * 47) % 360;
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 960 540" width="960" height="540">
<defs><filter id="b"><feGaussianBlur stdDeviation="${blur / 2}"/></filter></defs>
<rect width="960" height="540" fill="hsl(${hue} 35% 18%)"/>
<g filter="url(#b)">
<circle cx="${280 + seed * 30}" cy="270" r="150" fill="hsl(${(hue + 40) % 360} 70% 55%)"/>
<rect x="520" y="140" width="260" height="300" rx="24" fill="hsl(${(hue + 180) % 360} 60% 60%)"/>
<path d="M0 470 L960 400 L960 540 L0 540 Z" fill="hsl(${(hue + 90) % 360} 40% 30%)"/>
</g></svg>`;
}
