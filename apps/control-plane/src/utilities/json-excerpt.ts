// The excerpt is sent as a JSON string, so quotes and control characters count after escaping.
export function jsonExcerpt(source: string, offset: number, bytes: number): {
  text: string;
  next_offset: number | null;
} {
  let start = Math.min(offset, source.length);
  const first = source.charCodeAt(start);
  const previous = source.charCodeAt(start - 1);
  if (first >= 0xdc00 && first <= 0xdfff && previous >= 0xd800 && previous <= 0xdbff) start--;
  let low = start;
  let high = Math.min(source.length, start + bytes);
  while (low < high) {
    const middle = Math.ceil((low + high) / 2);
    const encoded = JSON.stringify(source.slice(start, middle));
    if (Buffer.byteLength(encoded) <= bytes) low = middle;
    else high = middle - 1;
  }
  const last = source.charCodeAt(low - 1);
  const next = source.charCodeAt(low);
  if (last >= 0xd800 && last <= 0xdbff && next >= 0xdc00 && next <= 0xdfff) low--;
  const excerpt = { text: source.slice(start, low), next_offset: low < source.length ? low : null };
  return excerpt;
}
