import { shorten } from "./shared.js";

export const xiaohongshuSource = {
  id: "xhs",
  name: "小红书",
  type: "social",
  label: "小",
  className: "source-xhs",
  logoDomain: "xiaohongshu.com",
  logoUrls: [
    "./assets/logos/xiaohongshu.ico",
    "https://www.xiaohongshu.com/favicon.ico",
  ],
  match({ host }) {
    return host.includes("xiaohongshu.com") || host.includes("xhslink.com");
  },
  buildTitle({ sharedTitle, metadataTitle }) {
    if (metadataTitle) return metadataTitle;
    if (sharedTitle) return sharedTitle;
    return "小红书收藏";
  },
  async crawl() {
    return { status: "not_implemented", source: "xhs" };
  },
};
