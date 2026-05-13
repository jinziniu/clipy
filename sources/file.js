export const fileSource = {
  id: "file",
  name: "文件",
  type: "file",
  label: "",
  className: "source-file",
  iconSvg: `
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M14 2H7a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V7z" />
      <path d="M14 2v5h5" />
    </svg>
  `,
  buildTitle({ entry }) {
    return entry.title || entry.fileName || "未命名文件";
  },
  classifyMime(type) {
    if (!type || type === "未知类型") return "未知类型";
    if (type.startsWith("image/")) return "图片";
    if (type.startsWith("video/")) return "视频";
    if (type.startsWith("audio/")) return "音频";
    if (type.startsWith("text/")) return "文本";
    if (type.includes("pdf")) return "PDF";
    if (type.includes("spreadsheet") || type.includes("excel")) return "Excel";
    if (type.includes("presentation") || type.includes("powerpoint")) return "PPT";
    if (type.includes("word") || type.includes("document")) return "Word";
    if (type.includes("zip") || type.includes("compressed")) return "压缩包";
    return type.split("/").pop().toUpperCase();
  },
  async crawl() {
    return { status: "not_implemented", source: "file" };
  },
};
