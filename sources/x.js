export const xSource = {
  id: "x",
  name: "X",
  type: "social",
  label: "X",
  className: "source-x",
  logoDomain: "x.com",
  iconSvg: `
    <svg class="source-brand-svg x-glyph" viewBox="0 0 44 44" aria-hidden="true">
      <text x="22" y="29" text-anchor="middle">X</text>
    </svg>
  `,
  match({ host }) {
    return host === "x.com" || host.endsWith(".x.com") || host.includes("twitter.com");
  },
  buildTitle({ sharedTitle, metadataTitle, pathParts }) {
    if (sharedTitle) return sharedTitle;
    if (metadataTitle) return metadataTitle;
    const user = pathParts[0] ? `@${pathParts[0]}` : "X";
    return pathParts.includes("status") ? `${user} 的帖子` : `${user} 的主页`;
  },
  async crawl() {
    return { status: "not_implemented", source: "x" };
  },
};
