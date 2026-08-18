// SPEC.md 4.6: captured_at is always stored in Asia/Kolkata. Format in that
// zone explicitly — Date objects carry no timezone, so a naive toISOString()
// would render it back in UTC and shift the displayed clock time.
export function formatCapturedAt(date: Date): string {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Kolkata',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).formatToParts(date);

  const get = (type: string) => parts.find((p) => p.type === type)?.value ?? '';
  return `${get('year')}-${get('month')}-${get('day')} ${get('hour')}:${get('minute')} IST`;
}
