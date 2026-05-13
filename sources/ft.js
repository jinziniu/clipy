import { genericWebTitle } from "./shared.js";

export const ftSource = {
  id: "ft",
  name: "FT",
  type: "article",
  label: "FT",
  className: "source-ft",
  logoDomain: "ft.com",
  logoUrls: ["https://www.ft.com/favicon.ico"],
  match({ host }) {
    return host === "ft.com" || host.endsWith(".ft.com");
  },
  buildTitle({ sharedTitle, metadataTitle, url, pathParts }) {
    return sharedTitle || metadataTitle || genericWebTitle({ url, pathParts });
  },
  async crawl() {
    return { status: "not_implemented", source: "ft" };
  },
};
