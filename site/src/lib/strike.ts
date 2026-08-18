import { hashString } from './hash';

// Hand-drawn wobble path for one rendered text line. Drawn per line rather
// than stretched across a title's full (possibly multi-line) box — a build
// -time SVG can't know how text will wrap on a given viewport, so the path
// is generated at runtime from the real measured line width and height
// (see client/strike-through.ts), in that line's own local pixel space with
// x running 0..width and y centred on the line's own height.
const UNITS_PER_HUMP = 55;

export function wobblePath(seed: string, width: number, height: number): string {
  const humps = Math.max(2, Math.round(width / UNITS_PER_HUMP));
  const baseSeed = Math.abs(hashString(seed));
  const baseline = height / 2;
  const amplitude = Math.min(height * 0.35, 9);

  const points: Array<{ x: number; y: number }> = [];
  for (let i = 0; i <= humps; i++) {
    const x = (width * i) / humps;
    const jitter = (Math.abs(hashString(`${baseSeed}-${i}`)) % 1000) / 1000 - 0.5; // -0.5..0.5
    points.push({ x, y: baseline + jitter * amplitude * 2 });
  }

  let d = `M${points[0].x.toFixed(1)},${points[0].y.toFixed(1)}`;
  for (let i = 1; i < points.length; i++) {
    const prev = points[i - 1];
    const curr = points[i];
    const midX = ((prev.x + curr.x) / 2).toFixed(1);
    d += ` Q${midX},${prev.y.toFixed(1)} ${curr.x.toFixed(1)},${curr.y.toFixed(1)}`;
  }
  return d;
}
