import { hashString } from './hash';

// SPEC.md §7: every image gets a deterministic tilt between -2.5 and +2.5
// degrees, hashed from the fragment id — not per media item, so a
// multi-image fragment's photos share one tilt.
export function tiltForId(id: string): number {
  const normalized = (Math.abs(hashString(id)) % 1000) / 1000; // 0..1
  const degrees = -2.5 + normalized * 5;
  return Math.round(degrees * 100) / 100;
}
