import { R2_PUBLIC_BASE } from 'astro:env/server';

export function mediaUrl(key: string): string {
  return `${R2_PUBLIC_BASE.replace(/\/+$/, '')}/${key.replace(/^\/+/, '')}`;
}
