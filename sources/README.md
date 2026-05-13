# Source handlers

Each source has one file in this folder. The UI calls `registry.js`, and the registry delegates source-specific behavior to the right handler.

Handler shape:

```js
export const exampleSource = {
  id: "example",
  name: "Example",
  type: "social",
  label: "E",
  className: "source-example",
  logoDomain: "example.com",
  logoUrls: ["https://www.example.com/favicon.ico"],
  match({ url, host }) {
    return host.includes("example.com");
  },
  buildTitle({ rawText, url, pathParts, lastPart, sharedTitle }) {
    return sharedTitle || "Example 收藏";
  },
  async crawl({ entry, options }) {
    return { status: "not_implemented", source: "example" };
  },
};
```

Future crawling and knowledge-base work should live here first:

- URL matching and source detection
- Official/common logos in `logos.js`
- Share-text parsing and title extraction
- Platform-specific crawling
- Platform-specific cleaning
- Conversion into LLM-readable records such as markdown, transcript, metadata, entities, and references

Logo display order:

1. Common local logo list in `logos.js`
2. Source-specific `logoUrls`, if a handler defines them
3. `/api/logo?url=...`, which asks the local server to read and cache the site's favicon/icon
4. Source fallback SVG or label

For binary inputs such as files, video, and audio, start in `file.js`. If one media type grows its own pipeline, split it into its own handler and register it in `registry.js`.

Backend content capture currently lives in `content_pipeline.py`. When a new source needs full-text capture, add the UI/source detection here first, then add the local Markdown capture logic there.
