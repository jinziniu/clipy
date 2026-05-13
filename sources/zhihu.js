export const zhihuSource = {
  id: "zhihu",
  name: "知乎",
  type: "social",
  label: "知",
  className: "source-zhihu",
  logoDomain: "zhihu.com",
  logoUrls: [
    "./assets/logos/zhihu.ico",
    "https://static.zhihu.com/heifetz/favicon.ico",
    "https://www.zhihu.com/favicon.ico",
  ],
  match({ host }) {
    return host.includes("zhihu.com");
  },
  buildTitle({ sharedTitle, pathParts, metadataTitle }) {
    if (metadataTitle) return metadataTitle;
    if (sharedTitle) return sharedTitle;
    if (pathParts[0] === "question" && pathParts[1]) return `知乎问题 ${pathParts[1]}`;
    if (pathParts[0] === "p" && pathParts[1]) return `知乎文章 ${pathParts[1]}`;
    return "知乎收藏";
  },
  async crawl() {
    return { status: "not_implemented", source: "zhihu" };
  },
};
