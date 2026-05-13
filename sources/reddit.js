import { genericWebTitle } from "./shared.js";

export const redditSource = {
  id: "reddit",
  name: "Reddit",
  type: "social",
  label: "R",
  className: "source-reddit",
  logoDomain: "reddit.com",
  logoUrls: ["https://www.redditstatic.com/desktop2x/img/favicon/favicon-96x96.png"],
  match({ host }) {
    return host === "reddit.com" || host.endsWith(".reddit.com");
  },
  buildTitle({ sharedTitle, metadataTitle, url, pathParts }) {
    return sharedTitle || metadataTitle || genericWebTitle({ url, pathParts });
  },
  async crawl() {
    return { status: "not_implemented", source: "reddit" };
  },
};
