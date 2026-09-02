import { hashString } from './hash';

// Rotation as exception (post-page rhythm): most images sit flat at 0°. Only
// ids whose hash falls in the top ~20% get a tilt — up to ±2° (halved from
// an earlier ±4°, which read as too much on the smaller medium thumbnails
// used on the homepage/journey grids). Fully deterministic from the id, never
// Math.random(), so a multi-image fragment's photos share one tilt and it's
// stable across builds.
export function tiltForId(id: string): number {
  const h = Math.abs(hashString(id));
  const normalized = (h % 1000) / 1000; // 0..1
  if (normalized < 0.8) return 0;
  const strength = (normalized - 0.8) / 0.2; // 0..1 within the top fifth
  const sign = h & 1 ? 1 : -1;
  return Math.round(sign * (1 + strength) * 100) / 100; // ±1°..±2°
}
