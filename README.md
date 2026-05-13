# Clipy 收藏夹

Clipy is a local-first bookmark and file collection app for building a personal knowledge base. It lets you save links and files, keeps a timeline of what you collected, and stores extracted content locally so a personal AI assistant can use it later.

## What Clipy Does

- Save links from social platforms, articles, GitHub repositories, videos, and general webpages.
- Save local files such as PDF, Word, Excel, PowerPoint, images, audio, and video.
- Show each item in a clean timeline with the source icon, title, source, time, and content status.
- Copy uploaded files into the local Clipy library instead of relying on the original path.
- Save text-oriented link content as Markdown and snapshots/assets locally when available.
- Open links through Clipy's own Chrome profile when a page needs user login or verification.
- Organize saved items into custom repositories without duplicating or deleting the original collection.

## Local Data

Clipy is designed to keep personal data on your machine. Runtime data is stored in `clipy_library/`, including:

- `bookmarks.json`
- uploaded file copies
- saved Markdown and HTML snapshots
- downloaded assets
- Clipy browser profile and login state

This folder is intentionally ignored by Git.

## Run Locally

```bash
python3 server.py
```

Then open:

```text
http://127.0.0.1:4173/
```

On macOS, you can also run `start.command`.

## Project Structure

```text
index.html                 # App shell
styles.css                 # UI styling
app.js                     # Frontend state and interaction logic
server.py                  # Local HTTP API and storage service
content_pipeline.py        # Link/file content extraction pipeline
item_pipeline.py           # Local item materialization helpers
browser_profile.py         # Clipy Chrome profile launcher and watcher
browser_profile_fetch.js   # Browser-side page capture helper
sources/                   # Source-specific handling logic
assets/logos/              # Built-in source icons
SOURCE_TITLE_AND_CONTENT_PLAN.md
```

## Notes

Clipy is for saving content that the user can personally access, one item at a time, into a local personal knowledge base. It is not intended for bulk scraping or bypassing access controls.
