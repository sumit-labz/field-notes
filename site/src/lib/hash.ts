// Shared deterministic string hash (FNV-1a) — used for image tilt and
// strike-through wobble. Never Math.random(): the same input must always
// produce the same output across builds. FNV-1a's multiplicative mixing
// matters here: the wobble hashes near-identical inputs like "<seed>-0",
// "<seed>-1", "<seed>-2" — a plain polynomial hash leaves those one apart
// and the wobble flattens into a straight line.
export function hashString(input: string): number {
  let hash = 0x811c9dc5; // FNV offset basis
  for (let i = 0; i < input.length; i++) {
    hash ^= input.charCodeAt(i);
    hash = Math.imul(hash, 0x01000193); // FNV prime
  }
  return hash | 0;
}
