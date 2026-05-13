export function extractSharedTitle(text, href) {
  const withoutUrls = text.replace(href, "").replace(/(?:https?:\/\/|www\.)[^\s"'<>]+/gi, "");
  const candidate = withoutUrls
    .split(/\r?\n/)
    .map((line) => line.trim())
    .map((line) =>
      line
        .replace(/^#+\s*/, "")
        .replace(/^【[^】]+】/, "")
        .replace(/复制.*$/, "")
        .replace(/打开.*$/, "")
        .replace(/快来看.*$/, "")
        .replace(/\s*\|\|\s*/g, " · ")
        .trim(),
    )
    .find((line) => line.length >= 2 && !/^https?:/i.test(line));

  return candidate ? shorten(candidate, 48) : "";
}

export function getPathParts(url) {
  return url.pathname.split("/").filter(Boolean).map(safeDecode);
}

export function safeDecode(value) {
  try {
    return decodeURIComponent(value);
  } catch {
    return value;
  }
}

export function shorten(value, maxLength) {
  if (!value || value.length <= maxLength) return value;
  return `${value.slice(0, maxLength - 3)}...`;
}

export function genericWebTitle({ url, pathParts }) {
  const host = url.hostname.replace(/^www\./, "");
  const slugTitle = titleFromSlug(pathParts);
  if (slugTitle) return slugTitle;
  const readablePath = pathParts.find((part) => !/^\d+$/.test(part));
  return readablePath ? `${host} / ${shorten(readablePath, 28)}` : host;
}

function titleFromSlug(pathParts) {
  const ignored = new Set([
    "news",
    "articles",
    "article",
    "markets",
    "technology",
    "business",
    "world",
    "opinion",
    "features",
  ]);

  for (const part of [...pathParts].reverse()) {
    const lower = part.toLowerCase();
    if (ignored.has(lower)) continue;
    if (/^\d{4}-\d{2}-\d{2}$|^\d+$/.test(lower)) continue;
    if (!part.includes("-") || !/[a-z\u4e00-\u9fff]/i.test(part)) continue;

    const words = part.split(/[-_]+/).filter(Boolean);
    if (words.length < 3) continue;
    return headlineCase(words);
  }
  return "";
}

function headlineCase(words) {
  const smallWords = new Set(["a", "an", "and", "as", "at", "but", "by", "for", "from", "in", "nor", "of", "on", "or", "per", "the", "to", "vs", "via", "with"]);
  return words
    .map((word, index) => {
      const lower = word.toLowerCase();
      if (index > 0 && smallWords.has(lower)) return lower;
      if (word === word.toUpperCase()) return word;
      return `${lower.slice(0, 1).toUpperCase()}${lower.slice(1)}`;
    })
    .join(" ");
}
