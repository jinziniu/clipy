import { genericWebTitle, shorten } from "./shared.js";

const RESERVED_PATHS = new Set([
  "about",
  "blog",
  "collections",
  "contact",
  "customer-stories",
  "enterprise",
  "events",
  "explore",
  "features",
  "login",
  "marketplace",
  "new",
  "notifications",
  "organizations",
  "pricing",
  "readme",
  "search",
  "settings",
  "signup",
  "sponsors",
  "topics",
  "trending",
]);

export const githubSource = {
  id: "github",
  name: "GitHub",
  type: "code",
  label: "GH",
  className: "source-github",
  logoDomain: "github.com",
  logoUrls: ["https://github.githubassets.com/favicons/favicon.svg"],
  match({ host }) {
    return host === "github.com" || host.endsWith(".github.com");
  },
  buildTitle({ sharedTitle, metadataTitle, url, pathParts }) {
    if (sharedTitle) return sharedTitle;
    if (metadataTitle) return cleanGithubTitle(metadataTitle);

    const repo = getGithubRepo(pathParts);
    if (!repo) return genericWebTitle({ url, pathParts });

    const detail = getGithubDetailTitle(pathParts, repo);
    return detail || `${repo.owner}/${repo.name}`;
  },
  async crawl() {
    return { status: "not_implemented", source: "github" };
  },
};

function getGithubRepo(pathParts) {
  if (pathParts.length < 2) return null;
  const owner = pathParts[0];
  const name = pathParts[1];
  if (!owner || !name || RESERVED_PATHS.has(owner.toLowerCase())) return null;
  return { owner, name };
}

function getGithubDetailTitle(pathParts, repo) {
  const section = pathParts[2] || "";
  const number = pathParts[3] || "";
  if ((section === "issues" || section === "pull") && /^\d+$/.test(number)) {
    const label = section === "pull" ? "PR" : "Issue";
    return `${repo.owner}/${repo.name} ${label} #${number}`;
  }
  if (section === "releases" && pathParts[3]) {
    return `${repo.owner}/${repo.name} Release ${shorten(pathParts.slice(3).join("/"), 30)}`;
  }
  if (section === "tree" && pathParts[3]) {
    return `${repo.owner}/${repo.name} @ ${shorten(pathParts[3], 24)}`;
  }
  if (section === "blob" && pathParts.length > 4) {
    return `${repo.owner}/${repo.name} / ${shorten(pathParts.slice(4).join("/"), 36)}`;
  }
  return "";
}

function cleanGithubTitle(title) {
  return title
    .replace(/^GitHub\s*-\s*/i, "")
    .replace(/\s*·\s*GitHub\s*$/i, "")
    .trim();
}
