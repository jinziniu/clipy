import { shorten } from "./shared.js";

export const dianpingSource = {
  id: "dianping",
  name: "大众点评",
  type: "social",
  label: "点评",
  className: "source-dianping",
  logoDomain: "dianping.com",
  iconSvg: `
    <svg class="source-brand-svg dianping-wordmark" viewBox="0 0 44 44" aria-hidden="true">
      <text x="22" y="27" text-anchor="middle">点评</text>
    </svg>
  `,
  match({ host }) {
    return host.includes("dianping.com") || host === "dpurl.cn" || host.endsWith(".dpurl.cn");
  },
  buildTitle({ sharedTitle, lastPart }) {
    if (sharedTitle) return sharedTitle;
    return lastPart ? `大众点评 ${shorten(lastPart, 18)}` : "大众点评收藏";
  },
  async crawl() {
    return { status: "not_implemented", source: "dianping" };
  },
};
