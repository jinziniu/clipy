export const wechatSource = {
  id: "wechat",
  name: "公众号",
  type: "article",
  label: "公",
  className: "source-wechat",
  logoDomain: "mp.weixin.qq.com",
  iconSvg: `
    <svg class="source-brand-svg wechat-glyph" viewBox="0 0 44 44" aria-hidden="true">
      <path d="M18.2 13.1c-6.2 0-11.2 3.9-11.2 8.8 0 2.8 1.6 5.3 4.2 6.9l-.9 3.2 3.7-1.8c1.3.4 2.7.6 4.2.6.6 0 1.2 0 1.8-.1a9.1 9.1 0 0 1-.4-2.7c0-5.1 4.9-9.3 11.1-9.7-1.5-3.1-6.3-5.2-12.5-5.2Z" />
      <path d="M31.2 20.6c-4.9 0-8.9 3.2-8.9 7.1s4 7.1 8.9 7.1c1 0 2-.1 2.9-.4l3 1.5-.7-2.6c2.2-1.3 3.6-3.3 3.6-5.6 0-3.9-4-7.1-8.8-7.1Z" />
      <circle cx="14.9" cy="20.4" r="1.2" />
      <circle cx="21.8" cy="20.4" r="1.2" />
      <circle cx="28.2" cy="26.6" r="1" />
      <circle cx="34.1" cy="26.6" r="1" />
    </svg>
  `,
  match({ host }) {
    return host === "mp.weixin.qq.com";
  },
  buildTitle({ sharedTitle, metadataTitle }) {
    if (sharedTitle) return sharedTitle;
    if (metadataTitle) return metadataTitle;
    return "公众号文章";
  },
  async crawl() {
    return { status: "not_implemented", source: "wechat" };
  },
};
