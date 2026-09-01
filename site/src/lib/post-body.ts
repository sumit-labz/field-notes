// Splits a post's raw markdown body into text/fragment blocks per SPEC.md 3.2:
// fragments render inline at {{fragment:ID}} markers if present, otherwise
// appended in listed order after the prose.

export type PostBlock = { kind: 'text'; html: string } | { kind: 'fragment'; id: string };

const MARKER_RE = /\{\{fragment:([a-zA-Z0-9-]+)\}\}/g;

function escapeHtml(text: string): string {
  return text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

// Bare URLs (the plain way of dropping a link into prose here — no markdown
// link syntax is parsed) render as inert text otherwise. Runs after escaping,
// so it only ever matches plain characters, never HTML already in the string.
const URL_RE = /https?:\/\/[^\s<]+[^\s<.,)]/g;
function autolink(html: string): string {
  return html.replace(URL_RE, (url) => `<a href="${url}" rel="noopener" target="_blank">${url}</a>`);
}

// A quoted aside (e.g. an AI reply worth keeping verbatim) — every line of the
// block prefixed with "> ", markdown-blockquote style. Blank lines *inside*
// the quote still need a lone "> " so the block-splitter above (which splits
// on blank lines) doesn't cut it into several pieces.
function isQuoteBlock(block: string): boolean {
  return block.split('\n').every((line) => line.trim() === '' || line.trimStart().startsWith('>'));
}
function stripQuoteMarkers(block: string): string {
  return block
    .split('\n')
    .map((line) => line.trimStart().replace(/^>\s?/, ''))
    .join('\n')
    .trim();
}

export function paragraphs(text: string): string {
  return text
    .split(/\n\s*\n/)
    .map((p) => p.trim())
    .filter(Boolean)
    .map((block) =>
      isQuoteBlock(block)
        ? `<blockquote class="ai-quote">${autolink(escapeHtml(stripQuoteMarkers(block)))}</blockquote>`
        : `<p>${autolink(escapeHtml(block))}</p>`
    )
    .join('\n');
}

export function splitPostBody(body: string, orderedFragmentIds: string[]): PostBlock[] {
  MARKER_RE.lastIndex = 0;
  const hasMarkers = MARKER_RE.test(body);
  MARKER_RE.lastIndex = 0;

  if (!hasMarkers) {
    const blocks: PostBlock[] = [];
    if (body.trim()) blocks.push({ kind: 'text', html: paragraphs(body) });
    for (const id of orderedFragmentIds) blocks.push({ kind: 'fragment', id });
    return blocks;
  }

  const blocks: PostBlock[] = [];
  let lastIndex = 0;
  let match: RegExpExecArray | null;
  while ((match = MARKER_RE.exec(body))) {
    const textChunk = body.slice(lastIndex, match.index);
    if (textChunk.trim()) blocks.push({ kind: 'text', html: paragraphs(textChunk) });
    blocks.push({ kind: 'fragment', id: match[1] });
    lastIndex = MARKER_RE.lastIndex;
  }
  const rest = body.slice(lastIndex);
  if (rest.trim()) blocks.push({ kind: 'text', html: paragraphs(rest) });
  return blocks;
}
