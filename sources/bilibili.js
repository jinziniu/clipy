export const bilibiliSource = {
  id: "bilibili",
  name: "哔哩哔哩",
  type: "video",
  label: "B",
  className: "source-bilibili",
  logoDomain: "bilibili.com",
  logoUrls: [
    "./assets/logos/bilibili.ico",
    "https://www.bilibili.com/favicon.ico",
  ],
  match({ host }) {
    return host === "bilibili.com" || host.endsWith(".bilibili.com") || host === "b23.tv";
  },
  buildTitle({ sharedTitle, metadataTitle }) {
    if (sharedTitle) return sharedTitle;
    if (metadataTitle) return metadataTitle;
    return "Bilibili 视频";
  },
  async crawl() {
    return { status: "not_implemented", source: "bilibili" };
  },
};
