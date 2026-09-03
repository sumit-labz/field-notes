// Renders a markdown-type fragment's body to real HTML at build time — the
// one place actual markdown parsing enters the codebase (post-body.ts is a
// hand-rolled paragraph splitter, deliberately not this). Scoped to markdown
// fragments only for now; see Fragment.astro.
import { marked } from 'marked';

marked.setOptions({ gfm: true, breaks: false });

export function renderMarkdown(source: string): string {
  return marked.parse(source, { async: false });
}
