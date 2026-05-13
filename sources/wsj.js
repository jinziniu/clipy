import { genericWebTitle } from "./shared.js";

export const wsjSource = {
  id: "wsj",
  name: "WSJ",
  type: "article",
  label: "WSJ",
  className: "source-wsj",
  logoDomain: "wsj.com",
  logoUrls: ["https://www.wsj.com/favicon.ico"],
  match({ host }) {
    return host === "wsj.com" || host.endsWith(".wsj.com");
  },
  buildTitle({ sharedTitle, metadataTitle, url, pathParts }) {
    return sharedTitle || metadataTitle || genericWebTitle({ url, pathParts });
  },
  async crawl() {
    return { status: "not_implemented", source: "wsj" };
  },
};
