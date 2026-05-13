import { shorten } from "./shared.js";

export const weiboSource = {
  id: "weibo",
  name: "微博",
  type: "social",
  label: "微",
  className: "source-weibo",
  logoDomain: "weibo.com",
  logoUrls: [
    "./assets/logos/weibo.ico",
    "https://weibo.com/favicon.ico",
    "https://www.weibo.com/favicon.ico",
  ],
  match({ host }) {
    return host.includes("weibo.com") || host.includes("weibo.cn");
  },
  buildTitle({ sharedTitle, metadataTitle, lastPart }) {
    if (sharedTitle) return sharedTitle;
    if (metadataTitle) return metadataTitle;
    return lastPart ? `微博 ${shorten(lastPart, 18)}` : "微博收藏";
  },
  async crawl() {
    return { status: "not_implemented", source: "weibo" };
  },
};
