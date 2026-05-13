const COMMON_LOGOS = [
  {
    sourceKeys: ["xhs"],
    domains: ["xiaohongshu.com", "xhslink.com"],
    urls: ["./assets/logos/xiaohongshu-ios.jpg"],
  },
  {
    sourceKeys: ["dianping"],
    domains: ["dianping.com", "dpurl.cn"],
    urls: ["./assets/logos/dianping-ios.jpg"],
  },
  {
    sourceKeys: ["bilibili"],
    domains: ["bilibili.com", "b23.tv"],
    urls: ["./assets/logos/bilibili-ios.jpg"],
  },
  {
    sourceKeys: ["weibo"],
    domains: ["weibo.com", "weibo.cn"],
    urls: ["./assets/logos/weibo-ios.jpg"],
  },
  {
    sourceKeys: ["x"],
    domains: ["x.com", "twitter.com"],
    urls: ["./assets/logos/x-ios.jpg"],
  },
  {
    sourceKeys: ["youtube"],
    domains: ["youtube.com", "youtu.be", "youtube-nocookie.com"],
    urls: ["./assets/logos/youtube-ios.jpg"],
  },
  {
    sourceKeys: ["wechat"],
    domains: ["mp.weixin.qq.com", "weixin.qq.com"],
    urls: ["./assets/logos/wechat-ios.jpg"],
  },
  {
    sourceKeys: ["zhihu"],
    domains: ["zhihu.com"],
    urls: ["./assets/logos/zhihu-ios.jpg"],
  },
  {
    sourceKeys: ["github"],
    domains: ["github.com"],
    urls: ["https://github.githubassets.com/favicons/favicon.svg"],
  },
  {
    sourceKeys: ["wsj"],
    domains: ["wsj.com"],
    urls: ["https://www.wsj.com/favicon.ico"],
  },
  {
    sourceKeys: ["ft"],
    domains: ["ft.com"],
    urls: ["https://www.ft.com/favicon.ico"],
  },
  {
    sourceKeys: ["reddit"],
    domains: ["reddit.com"],
    urls: ["https://www.redditstatic.com/desktop2x/img/favicon/favicon-96x96.png"],
  },
];

export function getCommonLogoCandidates(sourceKey, rawUrl) {
  const host = getHost(rawUrl);
  const match = COMMON_LOGOS.find((item) => {
    if (item.sourceKeys?.includes(sourceKey)) return true;
    return item.domains?.some((domain) => host === domain || host.endsWith(`.${domain}`));
  });

  return match ? match.urls : [];
}

function getHost(rawUrl) {
  try {
    return new URL(rawUrl).hostname.replace(/^www\./, "").toLowerCase();
  } catch {
    return "";
  }
}
