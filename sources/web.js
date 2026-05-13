import { genericWebTitle } from "./shared.js";

export const webSource = {
  id: "web",
  name: "网页",
  type: "web",
  label: "W",
  className: "source-web",
  logoDomain: "",
  buildTitle({ sharedTitle, metadataTitle, url, pathParts }) {
    return sharedTitle || metadataTitle || genericWebTitle({ url, pathParts });
  },
  async crawl() {
    return { status: "not_implemented", source: "web" };
  },
};
