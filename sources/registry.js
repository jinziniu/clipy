import { bilibiliSource } from "./bilibili.js";
import { dianpingSource } from "./dianping.js";
import { fileSource } from "./file.js";
import { ftSource } from "./ft.js";
import { githubSource } from "./github.js";
import { extractSharedTitle, getPathParts } from "./shared.js";
import { redditSource } from "./reddit.js";
import { webSource } from "./web.js";
import { wechatSource } from "./wechat.js";
import { weiboSource } from "./weibo.js";
import { wsjSource } from "./wsj.js";
import { xSource } from "./x.js";
import { xiaohongshuSource } from "./xiaohongshu.js";
import { youtubeSource } from "./youtube.js";
import { zhihuSource } from "./zhihu.js";

export { fileSource };

export const sourceHandlers = [
  xiaohongshuSource,
  dianpingSource,
  zhihuSource,
  xSource,
  youtubeSource,
  bilibiliSource,
  wechatSource,
  weiboSource,
  githubSource,
  wsjSource,
  ftSource,
  redditSource,
  fileSource,
  webSource,
];

const sourceById = Object.fromEntries(sourceHandlers.map((source) => [source.id, source]));

export function getSource(sourceKey) {
  return sourceById[sourceKey] || webSource;
}

export function detectSource(url) {
  const host = url.hostname.replace(/^www\./, "").toLowerCase();
  const source = sourceHandlers.find((handler) => {
    if (!handler.match) return false;
    return handler.match({ url, host });
  });

  return source ? source.id : webSource.id;
}

export function getEntrySourceKey(entry) {
  if (entry.kind !== "link" || !entry.url) return entry.sourceKey || fileSource.id;

  try {
    return detectSource(new URL(entry.url));
  } catch {
    return entry.sourceKey || webSource.id;
  }
}

export function getEntryTitle(entry, sourceKey = getEntrySourceKey(entry)) {
  if (entry.kind === "file" || entry.kind === "file-batch") {
    return fileSource.buildTitle({ entry });
  }

  if (entry.kind === "link" && entry.url) {
    try {
      const metadataTitle = isGenericTitle(entry.title, sourceKey) ? "" : entry.title;
      return getLinkTitle(entry.rawText || entry.url, new URL(entry.url), sourceKey, metadataTitle);
    } catch {
      return entry.title || "未命名收藏";
    }
  }

  return entry.title || "未命名收藏";
}

export function getLinkTitle(rawText, url, sourceKey = detectSource(url), metadataTitle = "") {
  const source = getSource(sourceKey);
  const pathParts = getPathParts(url);
  const lastPart = pathParts[pathParts.length - 1] || "";
  const sharedTitle = extractSharedTitle(rawText, url.href);

  if (source.buildTitle) {
    return source.buildTitle({ rawText, url, pathParts, lastPart, sharedTitle, metadataTitle });
  }

  return webSource.buildTitle({ rawText, url, pathParts, lastPart, sharedTitle, metadataTitle });
}

export async function crawlEntry(entry, options = {}) {
  const source = getSource(getEntrySourceKey(entry));
  if (!source.crawl) return { status: "not_supported", source: source.id };
  return source.crawl({ entry, options });
}

function isGenericTitle(title = "", sourceKey = "") {
  const trimmed = title.trim();
  const genericTitles = new Set([
    "",
    "X 收藏",
    "YouTube 视频",
    "YouTube 收藏",
    "Bilibili 视频",
    "公众号文章",
    "GitHub 仓库",
    "大众点评收藏",
    "小红书收藏",
    "微博收藏",
    "网页",
  ]);

  if (genericTitles.has(trimmed)) return true;
  if (sourceKey === "weibo" && /^微博\s+\d+$/.test(trimmed)) return true;
  if (/^[a-z0-9.-]+\s\/\s/i.test(trimmed)) return true;
  return false;
}
