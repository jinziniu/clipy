export const youtubeSource = {
  id: "youtube",
  name: "YouTube",
  type: "video",
  label: "",
  className: "source-youtube",
  logoDomain: "youtube.com",
  iconSvg: `
    <svg class="youtube-glyph" viewBox="0 0 24 24" aria-hidden="true">
      <rect x="2.5" y="5.5" width="19" height="13" rx="3.3" />
      <path d="m10.4 9 5 3-5 3z" />
    </svg>
  `,
  match({ host }) {
    return (
      host === "youtube.com" ||
      host.endsWith(".youtube.com") ||
      host === "youtu.be" ||
      host.endsWith(".youtu.be") ||
      host.includes("youtube-nocookie.com")
    );
  },
  buildTitle({ sharedTitle, metadataTitle, url, pathParts }) {
    if (sharedTitle) return sharedTitle;
    if (metadataTitle) return metadataTitle;
    if (url.hostname.includes("youtu.be")) return "YouTube 视频";
    if (pathParts[0] === "shorts") return "YouTube Shorts";
    if (pathParts[0] === "playlist") return "YouTube 播放列表";
    if (url.searchParams.has("v")) return "YouTube 视频";
    return "YouTube 收藏";
  },
  async crawl() {
    return { status: "not_implemented", source: "youtube" };
  },
};
