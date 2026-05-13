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
- Bumped static asset query strings for `app.js` and `styles.css`.

### Next Steps

1. Improve Xiaohongshu and other dynamic sites using rendered-page capture or Playwright fallback.
2. Add a separate raw snapshot action for archive inspection.
3. Improve reader typography and add AI summary to reader page.
4. Clean source-specific noise from more platforms.
6. Add highlight and note support.
7. Add embedding-based AI Q&A after summary is stable.
