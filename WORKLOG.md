# Clipy Worklog

## 2026-05-13

### Goal

Move Clipy toward a local-first read-it-later / AI reader MVP:

- Save links immediately.
- Parse and archive pages in the background.
- Generate searchable local content.
- Add optional AI summaries.
- Improve the real UI enough to test the flow end to end.

### Completed

#### Startup and Optional Clerk

- Made `clerk_app` optional in `server.py`.
- Main Clipy app now starts even when local `clerk/` is missing.
- `/clerk/*` endpoints return a clear unavailable response instead of crashing the server.

#### Immediate Save and Background Processing

- Changed URL save flow so `POST /api/entries/link` returns immediately with:
  - `processingStatus: queued`
  - `processingStep: queued`
- Added staged background status:
  - `queued`
  - `fetching`
  - `parsing`
  - `archiving`
  - `ready`
  - `failed`
- Frontend now displays these states as:
  - 排队中
  - 抓取中
  - 解析中
  - 归档中

#### Reparse

- Added:

```http
POST /api/entries/{id}/reparse
```

- Failed link entries can show a `重新解析` action.
- Reparse forces a fresh background processing run.

#### Page Capture and Archive Metadata

- Static page capture now stores raw HTML snapshots under `clipy_library/browser_snapshots/`.
- Link content can include:
  - `markdownPath`
  - `htmlPath`
  - `finalUrl`
  - `siteName`
  - `description`
- Existing item generation still writes:
  - `content.md`
  - `metadata.json`
  - `snapshot.html`

#### Article Extraction Improvements

- Added no-dependency HTML cleanup for:
  - script
  - style
  - noscript
  - nav
  - header
  - footer
  - aside
  - comment/sidebar/share/ad-like blocks
- Replaced first-match article extraction with scored candidate selection.
- Candidate scoring considers:
  - text length
  - paragraph count
  - punctuation density
  - image count
  - link-text penalty

#### Xiaohongshu Title Handling

- New Xiaohongshu entries no longer use note IDs as display titles.
- Xiaohongshu parsing now tries to extract true note titles from page JSON.
- Invalid site chrome titles such as `网上有害信息举报专区` and `小红书_沪ICP备` are ignored.
- If a rich Xiaohongshu snapshot already has a true note title, weaker static re-fetches should not overwrite it.
- Frontend has an extra fallback for Xiaohongshu titles using captured description or AI summary if title is still generic.

#### Local Search API

- Added:

```http
GET /api/search?q=keyword
```

- Current search checks:
  - title
  - URL
  - source name
  - source key
  - tags
  - generated Markdown content
- Search returns matching entries plus snippets.

#### Search UI

- Added a search input to the real page.
- Search is debounced by 250 ms.
- Active search replaces the timeline with search results.
- Clearing search restores the normal filter/repository view.
- Search results now display a local snippet under matching entries.
- Search snippets strip Markdown front matter and prefer content after `## 正文`.

#### Local Reader

- Added:

```http
GET /api/reader/{entry_id}
```

- Ready link entries now show a `阅读` action.
- Reader opens a local clean HTML view generated from `content.md`.
- Original raw page snapshot is no longer used as the primary reader view.
- Added reader asset routing for item-local images:

```http
GET /api/reader-assets/{entry_id}/{asset_name}
```

#### AI Summary MVP

- Added AI config loading from `.env` and environment variables:

```env
CLIPY_AI_API_KEY=
CLIPY_AI_MODEL=deepseek-chat
CLIPY_AI_BASE_URL=https://api.deepseek.com/v1
```

- Also supports OpenAI-style names:

```env
OPENAI_API_KEY=
OPENAI_MODEL=
OPENAI_BASE_URL=
```

- Added:

```http
GET /api/ai/status
POST /api/entries/{id}/summarize
```

- AI summary output is saved on the entry as:
  - `aiStatus`
  - `ai.summary`
  - `ai.keyPoints`
  - `ai.suggestedTags`
  - `ai.model`
  - `ai.updatedAt`
- DeepSeek config was verified as active.
- AI summary was tested successfully on an existing Xiaohongshu entry.

#### AI Summary UI

- Entries with ready content can show an `AI 总结` action.
- While running, the action shows `总结中`.
- Generated summaries can be expanded with `AI 摘要`.
- Expanded summaries can be hidden with `隐藏`.
- Summary card shows:
  - one-line summary
  - key points
  - suggested tags

#### Cache Busting

- Bumped static resource query strings in `index.html` several times so the browser loads updated `app.js`, `styles.css`, and source modules.

### Verified

- Python compile:

```bash
python3 -m py_compile server.py content_pipeline.py item_pipeline.py browser_profile.py rebuild_items.py
```

- Frontend syntax:

```bash
node --check app.js
```

- API checks:

```http
GET /api/entries
GET /api/ai/status
POST /api/entries/{id}/summarize
POST /api/entries/{id}/reparse
GET /api/search?q=...
```

- Confirmed:
  - `clerk_app` missing no longer breaks startup.
  - AI config returns configured with DeepSeek.
  - AI summary can move from `summarizing` to `ready`.
  - Search UI markup is served by the local server.

### Known Issues

- Xiaohongshu title extraction depends on rendered page JSON. If a weak static fetch overwrote an older rich snapshot before the protection was added, that entry may need `打开补全` or re-saving to recover the true original note title.
- `/api/search` works against current local data for title/body terms such as `Lagos`, `六合`, and `github`.
- Xiaohongshu static captures can still be weaker than rendered captures, but known legal/footer lines are now filtered during reparse.
- Search UI has been implemented, but visual browser QA still needs to be done after a hard refresh.
- Dynamic pages are still not handled by a dedicated Playwright fallback.
- Current background jobs are Python threads, not a durable queue.

### 2026-05-13 Zhihu Fix

- Fixed browser profile capture on local Node 20 by starting `browser_profile_fetch.js` with `--experimental-websocket`.
- Browser profile startup now removes stale Chrome `Singleton*` lock files before launching headless Chrome.
- Added Zhihu-specific rendered-page selectors for question/answer/article content.
- Added Zhihu JSON/static fallback and Zhihu page-noise filtering.
- Updated access detection so sufficiently captured Zhihu answers are not misclassified as `needs_login` because of inline login/expand UI text.
- Treat `知乎问题 <id>` and `知乎文章 <id>` as generic titles that can be replaced by real browser metadata.
- Reprocessed local entry `a7d6bd0c-bce6-4138-9bac-04d955a37836`; it is now `ready` with generated `content.md`.

### 2026-05-13 Reader and Refresh Fix

- Added frontend polling for entries while background parsing or AI summarization is still running, so optimistic titles such as `知乎问题 <id>` refresh to real titles without waiting for a manual reload.
- Reader pages now inject the entry's AI summary, key points, and suggested tags above the saved content when summary data is ready.
- Reader pages show an `AI 正在总结` state if opened while summarization is still running.
- Fixed Zhihu list-title rendering so frontend source handlers prefer `metadataTitle` / browser-captured title over fallback titles such as `知乎问题 <id>`.
- Replaced the reader endpoint's generic snapshot renderer with a dedicated reading renderer:
  - unified title/source/time header
  - AI summary above the article
  - article body starts at `## 正文`
  - no duplicated `content.md` front matter, title, source, URL, or saved-time lines
  - responsive typography for desktop and mobile
- Tightened Zhihu rendered capture for answer URLs:
  - extracts the target `.ContentItem.AnswerItem[name="<answer_id>"]`
  - uses the target answer's `.RichContent-inner` instead of the whole page
  - avoids saving related answers, sidebar/footer text, and QR/login images as article body
- Reprocessed local Zhihu entry `aff1f193-5c88-488d-bb7b-ad1740d21ad6`; its saved content now starts directly with the target answer text.
- Fixed local browser opens for reader and file preview by forcing `http://` on `localhost` / `127.0.0.1`, avoiding Chrome HTTPS auto-upgrade errors such as `ERR_SSL_PROTOCOL_ERROR`.
- Bumped static asset query strings for `app.js` and `styles.css`.

### 2026-05-13 Xiaohongshu Rendered Capture

- Xiaohongshu links no longer stop at a static `ready` result; they continue to try Clipy's browser profile so rendered note JSON can improve title/body/image capture.
- Browser-profile Xiaohongshu capture now prefers structured note data from page HTML:
  - cleaned note title
  - note `desc` as正文
  - `urlDefault` / `urlPre` images
- Weak Xiaohongshu browser results no longer overwrite an existing `ready` entry when the rendered page returns no structured note data and only a short safety/login text.
- Added detection for Xiaohongshu safety-limit pages such as `安全限制` / `IP存在风险`.
- Blocked metadata titles such as `安全限制` are dropped so they do not pollute list title rendering.
- Xiaohongshu frontend titles strip trailing `- 小红书`, including old entries that have not been rebuilt.
- Reprocessed local Xiaohongshu entry `ec5393f7-56d7-47b9-9d01-f36ff8e30086`; content was restored from the rich saved snapshot after a safety-limit response.

### 2026-05-14 Xiaohongshu Image Assets

- Xiaohongshu image URLs from saved/rendered page JSON are now normalized before download, including protocol-relative and HTTP CDN URLs.
- Image download now sends Xiaohongshu-specific referer headers and recognizes Xiaohongshu CDN suffixes such as `_jpg_3` / `_webp_3`.
- Browser-profile capture now tries to inline Xiaohongshu CDN images as `data:` payloads while the page can still access them, so later item generation can write local assets even if the CDN URL expires.
- Markdown image download now accepts `data:` payloads and preserves richer duplicate image records, such as a later browser image carrying inline bytes for the same URL.
- Existing Xiaohongshu snapshots are used as enrichment even when the current title is already good, so older rich snapshots can still provide missing images.
- Reprocessed local Xiaohongshu entries:
  - `ec5393f7-56d7-47b9-9d01-f36ff8e30086`: 6 images
  - `ca4532a0-3ed0-4ce1-977c-afc99f65e169`: 2 images
  - `75f9a161-e8bf-40cd-a003-307bb91f5f88`: 2 images
  - `2b26d1f5-75c1-4a97-bd9d-c086b86f1c57`: 10 images
- Xiaohongshu background capture can now reuse an already-open Clipy browser profile instead of failing with `visibleBrowserOpen`.
- Xiaohongshu safety-limit pages such as `安全限制` / `IP存在风险` are classified as blocked at the browser layer.
- If Xiaohongshu already has `ready` local content, a blocked safety page no longer overwrites the saved markdown/assets.
- Verified the local reader image route with a real Xiaohongshu item:
  - `GET /api/reader/2b26d1f5-75c1-4a97-bd9d-c086b86f1c57` returns reader HTML with `/api/reader-assets/...` image URLs.
  - `GET /api/reader-assets/2b26d1f5-75c1-4a97-bd9d-c086b86f1c57/image-01.jpg` returns `200 image/jpeg`.
- Profile-open and verification workers now mark an entry as complete when recovered content is already `ready`, even if the watched visible browser tab has since closed.

### Next Steps

1. Re-open the running app and visually confirm Xiaohongshu reader images render in Chrome.
2. Re-run Xiaohongshu profile-open recovery on entries whose `profileOpen.status` was previously marked `closed` even though content is ready.
3. Clean source-specific noise from more platforms.
4. Add highlight and note support.
5. Add embedding-based AI Q&A after summary is stable.
