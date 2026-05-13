#!/usr/bin/env python3
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from html import escape, unescape
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import parse_qs, quote, unquote, urljoin, urlparse
from urllib.request import Request, urlopen
from browser_profile import fetch_with_browser_profile, open_visible_browser_profile, summarize_browser_result
from content_pipeline import capture_browser_result_for_entry, capture_markdown_for_entry
from item_pipeline import build_item_for_entry, markdown_to_html, markdown_to_html_document, remove_entry_item, strip_front_matter, write_items_index
import gzip
import hashlib
import json
import mimetypes
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "clipy_library"
FILES_DIR = DATA_DIR / "files"
LOGOS_DIR = DATA_DIR / "logos"
MARKDOWN_DIR = DATA_DIR / "markdown"
MARKDOWN_ASSETS_DIR = DATA_DIR / "markdown_assets"
ITEMS_DIR = DATA_DIR / "items"
BROWSER_PROFILE_DIR = DATA_DIR / "browser_profile"
BOOKMARKS_FILE = DATA_DIR / "bookmarks.json"
BROWSER_REPOSITORIES_FILE = DATA_DIR / "repositories.json"
BROWSER_SESSIONS_FILE = DATA_DIR / "browser_sessions.json"
BROWSER_SESSION_CLEAR_SCRIPT = ROOT / "browser_session_clear.js"
MAX_METADATA_BYTES = 1_000_000
MAX_LOGO_BYTES = 500_000
STORAGE_LOCK = threading.RLock()
VERIFICATION_LOCK = threading.RLock()
VERIFICATION_WORKERS = set()
PROFILE_OPEN_LOCK = threading.RLock()
PROFILE_OPEN_WORKERS = set()
AI_SUMMARY_LOCK = threading.RLock()
AI_SUMMARY_WORKERS = set()
VERIFICATION_TIMEOUT_MS = 5 * 60 * 1000
VERIFICATION_POLL_SECONDS = 3
PROFILE_OPEN_REFRESH_DELAYS = [3, 10, 25, 60, 120]
AI_SUMMARY_MAX_CHARS = 16000
AI_SUMMARY_TIMEOUT = 45

sys.path.insert(0, str(ROOT / "clerk"))
CLERK_AVAILABLE = True
CLERK_IMPORT_ERROR = ""
try:
    from clerk_app.agent import answer as clerk_answer, list_articles as clerk_list_articles, search as clerk_search, source_index as clerk_source_index
    from clerk_app.chats import append_message as clerk_append_message, create_session as clerk_create_session, list_sessions as clerk_list_sessions, read_session as clerk_read_session
    from clerk_app.config import DEFAULT_BASE_URL as CLERK_DEFAULT_BASE_URL, DEFAULT_MODEL as CLERK_DEFAULT_MODEL, llm_config as clerk_llm_config
    from clerk_app.server import HTML as CLERK_HTML, WIKI_HTML as CLERK_WIKI_HTML
    from clerk_app.wiki import ingest_incremental as clerk_ingest_incremental, read_main_wiki as clerk_read_main_wiki, wiki_status as clerk_wiki_status
except Exception as error:
    CLERK_AVAILABLE = False
    CLERK_IMPORT_ERROR = str(error)
    CLERK_DEFAULT_BASE_URL = ""
    CLERK_DEFAULT_MODEL = ""
    CLERK_HTML = ""
    CLERK_WIKI_HTML = ""

SOURCE_NAMES = {
    "xhs": "小红书",
    "dianping": "大众点评",
    "zhihu": "知乎",
    "x": "X",
    "youtube": "YouTube",
    "bilibili": "哔哩哔哩",
    "wechat": "公众号",
    "weibo": "微博",
    "github": "GitHub",
    "wsj": "WSJ",
    "ft": "FT",
    "reddit": "Reddit",
    "web": "网页",
}


def ensure_storage():
    FILES_DIR.mkdir(parents=True, exist_ok=True)
    LOGOS_DIR.mkdir(parents=True, exist_ok=True)
    MARKDOWN_DIR.mkdir(parents=True, exist_ok=True)
    MARKDOWN_ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    ITEMS_DIR.mkdir(parents=True, exist_ok=True)
    BROWSER_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    if not BOOKMARKS_FILE.exists():
        write_entries([])
    if not BROWSER_REPOSITORIES_FILE.exists():
        BROWSER_REPOSITORIES_FILE.write_text("[]", encoding="utf-8")


def read_entries(enrich_links=False):
    ensure_storage()
    with STORAGE_LOCK:
        try:
            data = json.loads(BOOKMARKS_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            backup = BOOKMARKS_FILE.with_suffix(f".broken-{int(time.time())}.json")
            BOOKMARKS_FILE.rename(backup)
            data = []

    if not isinstance(data, list):
        return []

    entries = [normalize_entry_source(entry) for entry in data]
    entries = sorted(entries, key=lambda entry: entry.get("createdAt", 0), reverse=True)
    if enrich_links:
        entries = enrich_existing_links(entries)
    return entries


def write_entries(entries):
    with STORAGE_LOCK:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        temp_file = BOOKMARKS_FILE.with_suffix(".tmp")
        temp_file.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")
        temp_file.replace(BOOKMARKS_FILE)
        try:
            write_items_index(entries, DATA_DIR)
        except Exception:
            pass


def read_repositories():
    ensure_storage()
    with STORAGE_LOCK:
        try:
            data = json.loads(BROWSER_REPOSITORIES_FILE.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            data = []
    if not isinstance(data, list):
        return []
    return [normalize_repository(repo) for repo in data if isinstance(repo, dict)]


def write_repositories(repositories):
    with STORAGE_LOCK:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        normalized = [normalize_repository(repo) for repo in repositories if isinstance(repo, dict)]
        temp_file = BROWSER_REPOSITORIES_FILE.with_suffix(".tmp")
        temp_file.write_text(json.dumps(normalized, ensure_ascii=False, indent=2), encoding="utf-8")
        temp_file.replace(BROWSER_REPOSITORIES_FILE)


def normalize_repository(repo):
    repo = dict(repo or {})
    now = int(time.time() * 1000)
    name = sanitize_title(repo.get("name") or "新仓库") or "新仓库"
    entry_ids = repo.get("entryIds") or []
    if not isinstance(entry_ids, list):
        entry_ids = []
    seen = set()
    clean_entry_ids = []
    for entry_id in entry_ids:
        entry_id = str(entry_id or "").strip()
        if not entry_id or entry_id in seen:
            continue
        seen.add(entry_id)
        clean_entry_ids.append(entry_id)
    return {
        "id": safe_filename(repo.get("id")) or build_repository_id(name),
        "name": name,
        "entryIds": clean_entry_ids,
        "createdAt": safe_int(repo.get("createdAt"), now),
        "updatedAt": safe_int(repo.get("updatedAt"), safe_int(repo.get("createdAt"), now)),
    }


def build_repository_id(name="repo"):
    seed = f"{name}-{time.time()}".encode("utf-8")
    return f"repo-{int(time.time() * 1000)}-{hashlib.sha1(seed).hexdigest()[:6]}"


def create_repository(payload):
    now = int(time.time() * 1000)
    name = sanitize_title((payload or {}).get("name") or "新仓库") or "新仓库"
    entry_ids = filter_existing_entry_ids((payload or {}).get("entryIds") or [])
    repo = {
        "id": build_repository_id(name),
        "name": name,
        "entryIds": entry_ids,
        "createdAt": now,
        "updatedAt": now,
    }
    repositories = read_repositories()
    repositories.insert(0, repo)
    write_repositories(repositories)
    return repo


def update_repository(repo_id, payload):
    repositories = read_repositories()
    updated = None
    next_repositories = []
    for repo in repositories:
        if repo.get("id") != repo_id:
            next_repositories.append(repo)
            continue
        updated = dict(repo)
        if "name" in payload:
            name = sanitize_title(payload.get("name"))
            if not name:
                return None
            updated["name"] = name
        if "entryIds" in payload:
            updated["entryIds"] = filter_existing_entry_ids(payload.get("entryIds") or [])
        updated["updatedAt"] = int(time.time() * 1000)
        next_repositories.append(updated)

    if not updated:
        return None
    write_repositories(next_repositories)
    return updated


def delete_repository(repo_id):
    repositories = read_repositories()
    next_repositories = [repo for repo in repositories if repo.get("id") != repo_id]
    if len(next_repositories) == len(repositories):
        return False
    write_repositories(next_repositories)
    return True


def filter_existing_entry_ids(entry_ids):
    existing_ids = {entry.get("id") for entry in read_entries(enrich_links=False)}
    clean = []
    seen = set()
    for entry_id in entry_ids if isinstance(entry_ids, list) else []:
        entry_id = str(entry_id or "").strip()
        if entry_id in existing_ids and entry_id not in seen:
            clean.append(entry_id)
            seen.add(entry_id)
    return clean


def remove_entry_from_repositories(entry_id):
    repositories = read_repositories()
    changed = False
    next_repositories = []
    now = int(time.time() * 1000)
    for repo in repositories:
        entry_ids = repo.get("entryIds") or []
        if entry_id not in entry_ids:
            next_repositories.append(repo)
            continue
        updated = dict(repo)
        updated["entryIds"] = [item for item in entry_ids if item != entry_id]
        updated["updatedAt"] = now
        next_repositories.append(updated)
        changed = True
    if changed:
        write_repositories(next_repositories)


def search_entries(query, limit=30):
    query = clean_search_query(query)
    if not query:
        return []

    terms = [term for term in re.split(r"\s+", query.lower()) if term]
    results = []
    for entry in read_entries(enrich_links=False):
        haystack_parts = [
            entry.get("title") or "",
            entry.get("url") or "",
            entry.get("sourceName") or "",
            entry.get("sourceKey") or "",
            " ".join(entry.get("tags") or []) if isinstance(entry.get("tags"), list) else "",
        ]
        content_text = read_entry_search_text(entry)
        haystack = clean_inline_text(" ".join(haystack_parts + [content_text])).lower()
        if not all(term in haystack for term in terms):
            continue
        results.append(
            {
                "entry": entry,
                "snippet": build_search_snippet(content_text or entry.get("url") or entry.get("title") or "", terms),
            }
        )
        if len(results) >= limit:
            break
    return results


def clean_search_query(query):
    return clean_inline_text(str(query or ""))[:120]


def read_entry_search_text(entry):
    content_path = ((entry.get("item") or {}).get("contentPath") or (entry.get("content") or {}).get("markdownPath") or "")
    if not content_path:
        return ""
    path = (DATA_DIR / content_path).resolve()
    try:
        data_root = DATA_DIR.resolve()
        if data_root not in path.parents or not path.exists() or path.stat().st_size > MAX_METADATA_BYTES:
            return ""
        return strip_markdown_front_matter(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return ""


def strip_markdown_front_matter(text):
    text = re.sub(r"^---\s*\n.*?\n---\s*\n", "", text or "", count=1, flags=re.S)
    body_match = re.search(r"(^|\n)##\s+正文\s*\n+(.*)", text, re.S)
    if body_match:
        text = body_match.group(2)
    text = re.sub(r"!\[[^\]]*\]\([^)]+\)", "", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.M)
    return text


def build_search_snippet(text, terms, max_length=180):
    text = clean_inline_text(text)
    if not text:
        return ""
    lower_text = text.lower()
    positions = [lower_text.find(term) for term in terms if lower_text.find(term) >= 0]
    start = max(min(positions) - 60, 0) if positions else 0
    snippet = text[start : start + max_length].strip()
    if start > 0:
        snippet = f"...{snippet}"
    if start + max_length < len(text):
        snippet = f"{snippet}..."
    return snippet


def start_background_ai_summary(entry):
    if not ai_config().get("configured"):
        return False
    entry_id = entry.get("id")
    if not entry_id or (entry.get("item") or {}).get("status") != "ready":
        return False
    with AI_SUMMARY_LOCK:
        if entry_id in AI_SUMMARY_WORKERS:
            return False
        AI_SUMMARY_WORKERS.add(entry_id)
    worker = threading.Thread(target=ai_summary_worker, args=(entry_id,), daemon=True)
    worker.start()
    return True


def request_entry_ai_summary(entry_id):
    entry = next((item for item in read_entries(enrich_links=False) if item.get("id") == entry_id), None)
    if not entry:
        return None, {"error": "Entry not found"}, 404
    if not ai_config().get("configured"):
        if (entry.get("aiStatus") or (entry.get("ai") or {}).get("status")) == "summarizing":
            entry = mark_entry_ai_status(entry, "failed", "AI is not configured")
        return entry, {"error": "AI is not configured", "config": public_ai_config()}, 503
    if (entry.get("item") or {}).get("status") != "ready":
        return entry, {"error": "Entry content is not ready", "entry": entry}, 409

    entry = mark_entry_ai_status(entry, "summarizing")
    start_background_ai_summary(entry)
    return entry, {"entry": entry}, 202


def public_ai_config():
    config = ai_config()
    return {
        "configured": config.get("configured"),
        "baseUrl": config.get("base_url"),
        "model": config.get("model"),
    }


def mark_entry_ai_status(entry, status, error=""):
    updated = dict(entry)
    updated["aiStatus"] = status
    ai = dict(updated.get("ai") or {})
    ai["status"] = status
    ai["updatedAt"] = int(time.time() * 1000)
    if error:
        ai["error"] = error[:200]
    else:
        ai.pop("error", None)
    updated["ai"] = ai
    upsert_entry(updated)
    return updated


def ai_summary_worker(entry_id):
    try:
        entry = next((item for item in read_entries(enrich_links=False) if item.get("id") == entry_id), None)
        if not entry:
            return
        entry = mark_entry_ai_status(entry, "summarizing")
        content = read_entry_search_text(entry)
        if not content:
            mark_entry_ai_status(entry, "failed", "没有可总结的正文内容")
            return

        result = generate_ai_summary(entry, content[:AI_SUMMARY_MAX_CHARS])
        latest = next((item for item in read_entries(enrich_links=False) if item.get("id") == entry_id), entry)
        updated = dict(latest)
        updated["aiStatus"] = "ready"
        updated["ai"] = {
            "status": "ready",
            "summary": clean_inline_text(result.get("summary") or ""),
            "keyPoints": clean_string_list(result.get("keyPoints")),
            "suggestedTags": clean_string_list(result.get("suggestedTags")),
            "model": result.get("model") or ai_config().get("model"),
            "updatedAt": int(time.time() * 1000),
        }
        upsert_entry(updated)
    except Exception as error:
        entry = next((item for item in read_entries(enrich_links=False) if item.get("id") == entry_id), {"id": entry_id})
        mark_entry_ai_status(entry, "failed", str(error))
    finally:
        with AI_SUMMARY_LOCK:
            AI_SUMMARY_WORKERS.discard(entry_id)


def generate_ai_summary(entry, content):
    config = ai_config()
    prompt = {
        "title": entry.get("title") or "",
        "url": entry.get("url") or "",
        "content": content,
    }
    messages = [
        {
            "role": "system",
            "content": (
                "你是 Clipy 的阅读助手。请只输出 JSON，不要输出 Markdown。"
                "字段必须是 summary、keyPoints、suggestedTags。summary 用中文一句话。"
                "keyPoints 是 3 到 6 条中文要点，suggestedTags 是 3 到 6 个短标签。"
            ),
        },
        {
            "role": "user",
            "content": json.dumps(prompt, ensure_ascii=False),
        },
    ]
    payload = {
        "model": config["model"],
        "messages": messages,
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
    }
    response = post_ai_chat_completion(config, payload)
    content_text = (((response.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
    parsed = parse_json_object(content_text)
    parsed["model"] = response.get("model") or config["model"]
    return parsed


def post_ai_chat_completion(config, payload):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = Request(
        f"{config['base_url']}/chat/completions",
        data=body,
        headers={
            "Authorization": f"Bearer {config['api_key']}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=AI_SUMMARY_TIMEOUT) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"AI request failed: HTTP {error.code} {body[:160]}") from error


def parse_json_object(text):
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text or "", re.S)
        if not match:
            raise
        data = json.loads(match.group(0))
    return data if isinstance(data, dict) else {}


def clean_string_list(values):
    if not isinstance(values, list):
        return []
    clean = []
    seen = set()
    for value in values:
        text = clean_inline_text(str(value or ""))[:60]
        if text and text not in seen:
            clean.append(text)
            seen.add(text)
    return clean[:8]


def read_browser_session_state():
    ensure_storage()
    try:
        data = json.loads(BROWSER_SESSIONS_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        data = {}
    return data if isinstance(data, dict) else {}


def write_browser_session_state(state):
    ensure_storage()
    temp_file = BROWSER_SESSIONS_FILE.with_suffix(".tmp")
    temp_file.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    temp_file.replace(BROWSER_SESSIONS_FILE)


def load_local_env():
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
    except Exception:
        pass


def ai_config():
    api_key = os.environ.get("CLIPY_AI_API_KEY") or os.environ.get("OPENAI_API_KEY") or ""
    base_url = os.environ.get("CLIPY_AI_BASE_URL") or os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1"
    model = os.environ.get("CLIPY_AI_MODEL") or os.environ.get("OPENAI_MODEL") or "gpt-4o-mini"
    return {
        "configured": bool(api_key),
        "api_key": api_key,
        "base_url": base_url.rstrip("/"),
        "model": model,
    }


def upsert_entry(entry):
    with STORAGE_LOCK:
        entries = [item for item in read_entries() if item.get("id") != entry.get("id")]
        entries.append(entry)
        write_entries(sorted(entries, key=lambda item: item.get("createdAt", 0), reverse=True))


def update_entry(entry_id, fields):
    entries = read_entries()
    updated = None
    next_entries = []

    for entry in entries:
        if entry.get("id") == entry_id:
            updated = dict(entry)
            if "title" in fields:
                title = sanitize_title(fields.get("title"))
                if not title:
                    return None
                updated["title"] = title
                updated["renamedAt"] = int(time.time() * 1000)
            next_entries.append(updated)
        else:
            next_entries.append(entry)

    if not updated:
        return None

    write_entries(sorted(next_entries, key=lambda item: item.get("createdAt", 0), reverse=True))
    return updated


def remove_entry(entry_id):
    entries = read_entries()
    removed = next((entry for entry in entries if entry.get("id") == entry_id), None)
    write_entries([entry for entry in entries if entry.get("id") != entry_id])

    if removed and removed.get("kind") in {"file", "file-batch"}:
        file_dir = FILES_DIR / entry_id
        if file_dir.exists() and file_dir.is_dir():
            shutil.rmtree(file_dir)

    if removed and removed.get("kind") == "link":
        remove_entry_markdown(removed)

    if removed:
        remove_entry_item(removed, DATA_DIR)
        remove_entry_from_repositories(entry_id)

    return removed is not None


def remove_batch_file(entry_id, file_id):
    entries = read_entries()
    updated_entry = None
    removed_file = None
    next_entries = []

    for entry in entries:
        if entry.get("id") != entry_id:
            next_entries.append(entry)
            continue

        if entry.get("kind") != "file-batch":
            return None, None

        files = list(entry.get("files") or [])
        removed_file = next((file for file in files if file.get("id") == file_id), None)
        if not removed_file:
            return None, None

        remaining_files = [file for file in files if file.get("id") != file_id]
        if not remaining_files:
            remove_entry_item(entry, DATA_DIR)
            updated_entry = None
            continue

        if len(remaining_files) == 1:
            only_file = dict(remaining_files[0])
            updated_entry = {
                "id": entry_id,
                "kind": "file",
                "sourceKey": "file",
                "sourceName": "文件",
                "title": only_file.get("fileName") or entry.get("title") or "文件",
                "fileName": only_file.get("fileName"),
                "fileType": only_file.get("fileType") or "未知类型",
                "fileSize": only_file.get("fileSize", 0),
                "filePath": only_file.get("filePath"),
                "createdAt": entry.get("createdAt", int(time.time() * 1000)),
                "batchOrder": 0,
            }
        else:
            updated_entry = dict(entry)
            updated_entry["files"] = sorted(remaining_files, key=lambda item: item.get("batchOrder", 0))
            updated_entry["fileCount"] = len(remaining_files)
            updated_entry["expectedFileCount"] = len(remaining_files)
            updated_entry["fileSize"] = sum(item.get("fileSize", 0) for item in remaining_files)

        if updated_entry:
            updated_entry = build_item_for_entry(updated_entry, DATA_DIR)

        next_entries.append(updated_entry)

    if not removed_file:
        return None, None

    remove_file_asset(entry_id, removed_file)
    write_entries(sorted(next_entries, key=lambda item: item.get("createdAt", 0), reverse=True))
    return updated_entry, removed_file


def remove_file_asset(entry_id, file_record):
    file_path = (DATA_DIR / file_record.get("filePath", "")).resolve()
    if is_data_file(file_path):
        file_path.unlink()

    file_dir = (FILES_DIR / entry_id / file_record.get("id", "")).resolve()
    batch_root = (FILES_DIR / entry_id).resolve()
    if file_record.get("id") and file_dir.exists() and file_dir.is_dir() and batch_root in file_dir.parents:
        shutil.rmtree(file_dir)


def remove_entry_markdown(entry):
    content = entry.get("content") or {}
    markdown_path = (DATA_DIR / content.get("markdownPath", "")).resolve()
    if content.get("markdownPath") and markdown_path.exists() and DATA_DIR.resolve() in markdown_path.parents:
        markdown_path.unlink()

    assets_path = (DATA_DIR / content.get("assetsPath", "")).resolve()
    if content.get("assetsPath") and assets_path.exists() and DATA_DIR.resolve() in assets_path.parents:
        shutil.rmtree(assets_path)

    html_path = (DATA_DIR / content.get("htmlPath", "")).resolve()
    if content.get("htmlPath") and html_path.exists() and DATA_DIR.resolve() in html_path.parents:
        html_path.unlink()


def list_browser_sessions():
    state = read_browser_session_state()
    required_info = session_required_entry_info_from_entries()
    cookie_sessions = {session["siteKey"]: session for session in build_sessions_from_cookies(state, required_info)}
    sessions_by_key = {}

    for site_key, info in required_info.items():
        cookie_session = cookie_sessions.get(site_key) or {}
        saved = state.get(site_key) if isinstance(state.get(site_key), dict) else {}
        sessions_by_key[site_key] = {
            "siteKey": site_key,
            "host": site_key,
            "origin": saved.get("origin") or origin_for_site(site_key),
            "displayName": info.get("displayName") or saved.get("displayName") or site_key,
            "sourceKey": info.get("sourceKey") or saved.get("sourceKey") or detect_source_key(origin_for_site(site_key)),
            "recentUrl": info.get("recentUrl") or saved.get("recentUrl") or "",
            "recentTitle": info.get("recentTitle") or saved.get("recentTitle") or "",
            "requiredReason": info.get("requiredReason") or saved.get("requiredReason") or "",
            "requiredAt": info.get("requiredAt") or saved.get("requiredAt") or 0,
            "status": saved.get("status") or "logged_out",
            "cookieCount": cookie_session.get("cookieCount", 0),
            "authCookieCount": cookie_session.get("authCookieCount", 0),
            "lastAccessedAt": cookie_session.get("lastAccessedAt") or info.get("recentAt") or saved.get("loggedOutAt") or 0,
            "updatedAt": cookie_session.get("updatedAt") or saved.get("updatedAt") or info.get("recentAt") or saved.get("loggedOutAt") or 0,
            "cookieNames": cookie_session.get("cookieNames") or [],
        }

    result = []
    for session in sessions_by_key.values():
        saved = state.get(session["siteKey"]) or {}
        status = saved.get("status") if isinstance(saved, dict) else ""
        if status == "logged_out":
            session["status"] = "logged_out"
            session["loggedOutAt"] = saved.get("loggedOutAt")
            session["cookieCount"] = 0
            session["authCookieCount"] = 0
        elif status == "login_pending":
            if session.get("authCookieCount"):
                session["status"] = "logged_in"
            else:
                session["status"] = "login_pending"
                session["loginStartedAt"] = saved.get("loginStartedAt")
        else:
            session["status"] = "logged_in" if session.get("authCookieCount") else "logged_out"
        result.append(session)

    result.sort(key=lambda item: (item.get("status") != "logged_in", -(item.get("lastAccessedAt") or item.get("updatedAt") or 0), item.get("siteKey") or ""))
    return result


def build_sessions_from_cookies(state, entry_info=None):
    cookies = read_browser_cookies()
    by_site = {}
    entry_info = entry_info or session_entry_info_from_entries()
    for cookie in cookies:
        site_key = site_key_for_host(cookie.get("host") or "")
        if not site_key:
            continue
        site_key = session_alias_key(site_key, entry_info)
        info = entry_info.get(site_key) or {}
        session = by_site.setdefault(
            site_key,
            {
                "siteKey": site_key,
                "host": site_key,
                "origin": origin_for_site(site_key),
                "displayName": info.get("displayName") or site_key,
                "sourceKey": info.get("sourceKey") or detect_source_key(origin_for_site(site_key)),
                "recentUrl": info.get("recentUrl") or "",
                "recentTitle": info.get("recentTitle") or "",
                "status": "logged_out",
                "cookieCount": 0,
                "authCookieCount": 0,
                "lastAccessedAt": 0,
                "updatedAt": 0,
                "cookieNames": [],
            },
        )
        session["cookieCount"] += 1
        if is_auth_cookie(cookie.get("name") or "", site_key):
            session["authCookieCount"] += 1
        session["lastAccessedAt"] = max(session["lastAccessedAt"], chrome_time_to_ms(cookie.get("lastAccessUtc") or 0))
        session["updatedAt"] = max(session["updatedAt"], chrome_time_to_ms(cookie.get("lastUpdateUtc") or 0))
        if len(session["cookieNames"]) < 8:
            session["cookieNames"].append(cookie.get("name") or "")

    known_sites = set(entry_info.keys())

    for site_key, saved in state.items():
        if not isinstance(saved, dict):
            continue
        if site_key in by_site and saved.get("displayName"):
            by_site[site_key]["displayName"] = saved["displayName"]

    return [session for session in by_site.values() if session.get("siteKey") in known_sites]


def read_browser_cookies():
    cookies_path = browser_cookies_path()
    if not cookies_path:
        return []
    try:
        connection = sqlite3.connect(f"file:{cookies_path}?mode=ro", uri=True, timeout=2)
        rows = connection.execute(
            "select host_key, name, last_access_utc, last_update_utc, expires_utc, is_persistent from cookies"
        ).fetchall()
        connection.close()
    except Exception:
        return []
    return [
        {
            "host": row[0],
            "name": row[1],
            "lastAccessUtc": row[2],
            "lastUpdateUtc": row[3],
            "expiresUtc": row[4],
            "isPersistent": row[5],
        }
        for row in rows
    ]


def browser_cookies_path():
    candidates = [
        BROWSER_PROFILE_DIR / "Default" / "Network" / "Cookies",
        BROWSER_PROFILE_DIR / "Default" / "Cookies",
    ]
    return next((path for path in candidates if path.exists()), None)


def is_common_user_session_site(site_key):
    site_key = site_key_for_host(site_key)
    common_sites = {
        "x.com",
        "twitter.com",
        "weibo.com",
        "weibo.cn",
        "m.weibo.cn",
        "bilibili.com",
        "youtube.com",
        "youtu.be",
        "mp.weixin.qq.com",
        "weixin.qq.com",
        "zhihu.com",
        "xiaohongshu.com",
        "xhslink.com",
        "dianping.com",
        "dpurl.cn",
        "bloomberg.com",
        "github.com",
        "linkedin.com",
    }
    return site_key in common_sites


def session_display_names_from_entries():
    return {site_key: info.get("displayName") for site_key, info in session_entry_info_from_entries().items()}


def session_entry_info_from_entries():
    info_by_site = {}
    for entry in read_entries(enrich_links=False):
        if entry.get("kind") != "link" or not entry.get("url"):
            continue
        host = urlparse(entry.get("url")).hostname or ""
        site_key = site_key_for_host(host)
        if not site_key or site_key in info_by_site:
            continue
        source_key = entry.get("sourceKey") or detect_source_key(entry.get("url") or "")
        info_by_site[site_key] = {
            "displayName": entry.get("sourceName") if source_key != "web" else site_key,
            "sourceKey": source_key,
            "recentUrl": entry.get("url") or "",
            "recentTitle": entry.get("title") or "",
            "recentAt": entry.get("createdAt") or 0,
        }
    return info_by_site


def session_required_entry_info_from_entries():
    info_by_site = {}
    for entry in read_entries(enrich_links=False):
        if entry.get("kind") != "link" or not entry.get("url"):
            continue
        reason = login_requirement_reason(entry)
        if not reason:
            continue
        host = urlparse(entry.get("url")).hostname or ""
        site_key = canonical_login_site_key(host, entry.get("sourceKey"))
        if not site_key:
            continue
        source_key = entry.get("sourceKey") or detect_source_key(entry.get("url") or "")
        created_at = entry.get("createdAt") or 0
        existing = info_by_site.get(site_key)
        if existing and existing.get("recentAt", 0) >= created_at:
            continue
        info_by_site[site_key] = {
            "displayName": entry.get("sourceName") if source_key != "web" else site_key,
            "sourceKey": source_key,
            "recentUrl": entry.get("url") or "",
            "recentTitle": entry.get("title") or "",
            "recentAt": created_at,
            "requiredReason": reason,
            "requiredAt": login_requirement_at(entry) or created_at,
        }
    return info_by_site


def login_requirement_reason(entry):
    content = entry.get("content") or {}
    browser = entry.get("browserCapture") or {}
    verification = entry.get("verification") or {}
    content_status = content.get("status") or ""
    browser_status = browser.get("status") or ""
    verification_status = verification.get("status") or ""
    if content_status == "ready":
        return ""
    if content_status == "needs_login" or browser_status == "login_required":
        return "需要登录"
    if content_status == "blocked" or browser_status == "blocked":
        return "需要验证"
    if verification_status in {"waiting", "expired", "open_failed"}:
        return "等待登录或验证"
    if verification_status == "complete" and verification.get("attempt"):
        return "曾需要登录或验证"
    return ""


def login_requirement_at(entry):
    content = entry.get("content") or {}
    verification = entry.get("verification") or {}
    return (
        verification.get("startedAt")
        or verification.get("completedAt")
        or verification.get("lastCheckedAt")
        or content.get("capturedAt")
        or entry.get("createdAt")
        or 0
    )


def canonical_login_site_key(host, source_key=""):
    host = site_key_for_host(host)
    source_key = source_key or detect_source_key(origin_for_site(host))
    if source_key == "x" and host in {"twitter.com", "x.com"}:
        return "x.com"
    if source_key == "dianping":
        return host if host == "dpurl.cn" else "dianping.com"
    if source_key == "xhs":
        return host if host == "xhslink.com" else "xiaohongshu.com"
    return host


def session_alias_key(site_key, required_info):
    site_key = site_key_for_host(site_key)
    required_keys = set(required_info.keys())
    if site_key == "twitter.com" and "x.com" in required_keys:
        return "x.com"
    if site_key == "x.com" and "twitter.com" in required_keys:
        return "twitter.com"
    if site_key == "dianping.com" and "dpurl.cn" in required_keys:
        return "dpurl.cn"
    if site_key == "dpurl.cn" and "dianping.com" in required_keys:
        return "dianping.com"
    if site_key == "xiaohongshu.com" and "xhslink.com" in required_keys:
        return "xhslink.com"
    if site_key == "xhslink.com" and "xiaohongshu.com" in required_keys:
        return "xiaohongshu.com"
    return site_key


def site_key_for_host(host):
    host = (host or "").strip().lower().lstrip(".")
    if not host or host == "localhost":
        return ""
    if host.startswith("www."):
        host = host[4:]
    return host


def origin_for_site(site_key):
    site_key = site_key_for_host(site_key)
    return f"https://{site_key}" if site_key else ""


def is_auth_cookie(name, site_key=""):
    name = (name or "").lower()
    site_key = site_key_for_host(site_key)
    site_specific = {
        "github.com": {"user_session", "dotcom_user", "logged_in"},
        "x.com": {"auth_token", "ct0", "twid"},
        "twitter.com": {"auth_token", "ct0", "twid"},
        "bilibili.com": {"sessdata", "dedeuserid", "bili_jct"},
        "weibo.com": {"sub", "subp", "sso_login_status", "login_sid_t"},
        "weibo.cn": {"sub", "subp", "sso_login_status", "login_sid_t"},
        "m.weibo.cn": {"sub", "subp", "sso_login_status", "login_sid_t"},
        "zhihu.com": {"z_c0", "_zap"},
        "youtube.com": {"sid", "hsid", "ssid", "apisid", "sapisid", "__secure-1psid", "__secure-3psid"},
        "xiaohongshu.com": {"web_session", "customerclientid", "access-token"},
        "dianping.com": {"dper", "dplet", "ctu", "ua"},
        "bloomberg.com": {"bb-at", "bb-refresh", "login", "session"},
        "linkedin.com": {"li_at", "jsessionid"},
    }
    for domain, cookies in site_specific.items():
        if site_key == domain or site_key.endswith(f".{domain}"):
            return name in cookies or any(signal in name for signal in cookies if len(signal) > 5)

    generic_signals = ["auth", "token", "login", "passport", "account", "remember", "logged_in"]
    if any(signal in name for signal in generic_signals):
        return True
    return name in {"session", "sid", "ssid"} or name.endswith("_session") or name.endswith("_sess")


def chrome_time_to_ms(value):
    try:
        value = int(value)
    except (TypeError, ValueError):
        return 0
    if value <= 0:
        return 0
    return int((value / 1_000_000 - 11644473600) * 1000)


def logout_browser_session(site_key):
    site_key = site_key_for_host(site_key)
    if not site_key:
        return {"ok": False, "error": "Missing site key"}, 400

    state = read_browser_session_state()
    removed_cookies = delete_cookies_for_site(site_key)
    devtools_result = clear_site_data_with_devtools(site_key)
    now = int(time.time() * 1000)
    info = session_entry_info_from_entries().get(site_key) or {}
    display_name = (state.get(site_key) or {}).get("displayName") or info.get("displayName") or site_key
    state[site_key] = {
        "status": "logged_out",
        "host": site_key,
        "origin": origin_for_site(site_key),
        "displayName": display_name,
        "sourceKey": info.get("sourceKey") or detect_source_key(origin_for_site(site_key)),
        "recentUrl": info.get("recentUrl") or (state.get(site_key) or {}).get("recentUrl") or "",
        "recentTitle": info.get("recentTitle") or (state.get(site_key) or {}).get("recentTitle") or "",
        "loggedOutAt": now,
        "updatedAt": now,
    }
    write_browser_session_state(state)
    return {
        "ok": True,
        "siteKey": site_key,
        "removedCookies": removed_cookies,
        "devtools": devtools_result,
        "sessions": list_browser_sessions(),
    }, 200


def login_browser_session(site_key, target_url=""):
    site_key = site_key_for_host(site_key)
    if not site_key:
        return {"ok": False, "error": "Missing site key"}, 400
    state = read_browser_session_state()
    now = int(time.time() * 1000)
    origin = origin_for_site(site_key)
    info = session_required_entry_info_from_entries().get(site_key) or session_entry_info_from_entries().get(site_key) or {}
    target_url = normalize_session_target_url(target_url, site_key) or info.get("recentUrl") or origin
    opened = open_visible_browser_profile(target_url, DATA_DIR)
    display_name = (state.get(site_key) or {}).get("displayName") or info.get("displayName") or site_key
    state[site_key] = {
        "status": "login_pending" if opened.get("ok") else "logged_out",
        "host": site_key,
        "origin": origin,
        "displayName": display_name,
        "sourceKey": info.get("sourceKey") or detect_source_key(origin),
        "recentUrl": info.get("recentUrl") or target_url or (state.get(site_key) or {}).get("recentUrl") or "",
        "recentTitle": info.get("recentTitle") or (state.get(site_key) or {}).get("recentTitle") or "",
        "requiredReason": info.get("requiredReason") or (state.get(site_key) or {}).get("requiredReason") or "",
        "requiredAt": info.get("requiredAt") or (state.get(site_key) or {}).get("requiredAt") or now,
        "loginStartedAt": now,
        "updatedAt": now,
        "openResult": opened,
    }
    write_browser_session_state(state)
    return {"ok": bool(opened.get("ok")), "siteKey": site_key, "openResult": opened, "sessions": list_browser_sessions()}, 200 if opened.get("ok") else 500


def normalize_session_target_url(target_url, site_key):
    if not target_url:
        return ""
    try:
        parsed = urlparse(target_url)
    except Exception:
        return ""
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return ""
    target_site = canonical_login_site_key(parsed.hostname, detect_source_key(target_url))
    if target_site == site_key or site_key_for_host(parsed.hostname) == site_key:
        return target_url
    return ""


def delete_cookies_for_site(site_key):
    cookies_path = browser_cookies_path()
    if not cookies_path:
        return 0
    patterns = cookie_host_patterns(site_key)
    try:
        connection = sqlite3.connect(cookies_path, timeout=5)
        before = connection.total_changes
        connection.execute(
            "delete from cookies where host_key in ({}) or host_key like ?".format(",".join("?" for _ in patterns)),
            [*patterns, f"%.{site_key}"],
        )
        connection.commit()
        removed = connection.total_changes - before
        connection.close()
        return removed
    except Exception:
        return 0


def cookie_host_patterns(site_key):
    site_key = site_key_for_host(site_key)
    patterns = {site_key, f".{site_key}", f"www.{site_key}", f".www.{site_key}"}
    return sorted(patterns)


def clear_site_data_with_devtools(site_key):
    if not BROWSER_SESSION_CLEAR_SCRIPT.exists():
        return {"ok": False, "reason": "browser_session_clear.js missing"}
    try:
        result = subprocess.run(
            ["node", str(BROWSER_SESSION_CLEAR_SCRIPT), site_key],
            cwd=str(ROOT),
            text=True,
            capture_output=True,
            timeout=12,
        )
        payload = (result.stdout or "").strip().splitlines()[-1] if result.stdout.strip() else ""
        data = json.loads(payload) if payload else {"ok": False, "reason": result.stderr.strip()[:200]}
        if result.returncode != 0:
            data["ok"] = False
            data.setdefault("reason", result.stderr.strip()[:200] or f"exit {result.returncode}")
        return data
    except Exception as error:
        return {"ok": False, "reason": str(error)[:200]}


def safe_filename(name):
    cleaned = unquote(name or "clipy-file").strip().replace("/", "_").replace("\\", "_")
    cleaned = re.sub(r"[\x00-\x1f]+", "", cleaned)
    return cleaned or "clipy-file"


def sanitize_title(value):
    cleaned = unquote(str(value or "")).strip()
    cleaned = re.sub(r"[\x00-\x1f]+", " ", cleaned)
    return cleaned[:240]


def safe_int(value, fallback=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def build_file_batch_title(files, expected_count=None):
    count = expected_count or len(files)
    first_name = (files[0] or {}).get("fileName") if files else "文件"
    if count <= 1:
        return first_name or "文件"
    return f"{first_name} 等 {count} 个文件"


def split_entry_file_id(raw_id):
    parts = str(raw_id or "").split("/", 1)
    entry_id = unquote(parts[0])
    file_id = unquote(parts[1]) if len(parts) > 1 else None
    return entry_id, file_id


def get_file_record(entry, file_id=None):
    if entry.get("kind") == "file":
        return entry

    files = entry.get("files") or []
    if file_id:
        return next((item for item in files if item.get("id") == file_id), None)
    if len(files) == 1:
        return files[0]
    return None


def is_data_file(file_path):
    data_root = DATA_DIR.resolve()
    return file_path.exists() and file_path.is_file() and data_root in file_path.parents


def content_disposition(value):
    ascii_name = value.encode("ascii", "ignore").decode("ascii").strip() or "clipy-file"
    return f'inline; filename="{ascii_name}"; filename*=UTF-8\'\'{quote(value)}'


def json_response(handler, payload, status=200):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Access-Control-Allow-Methods", "GET, POST, PATCH, DELETE, OPTIONS")
    handler.send_header("Access-Control-Allow-Headers", "Content-Type, X-Clipy-Id, X-Clipy-File-Id, X-File-Name, X-File-Type")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def text_response(handler, message, status=200):
    body = message.encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "text/plain; charset=utf-8")
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Access-Control-Allow-Methods", "GET, POST, PATCH, DELETE, OPTIONS")
    handler.send_header("Access-Control-Allow-Headers", "Content-Type, X-Clipy-Id, X-Clipy-File-Id, X-File-Name, X-File-Type")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def html_response(handler, message, status=200):
    body = message.encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Access-Control-Allow-Methods", "GET, POST, PATCH, DELETE, OPTIONS")
    handler.send_header("Access-Control-Allow-Headers", "Content-Type, X-Clipy-Id, X-Clipy-File-Id, X-File-Name, X-File-Type")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def binary_response(handler, body, content_type, status=200):
    handler.send_response(status)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "public, max-age=86400")
    handler.end_headers()
    handler.wfile.write(body)


def reader_html_from_markdown(entry, markdown):
    title_text = sanitize_title(entry.get("title") or "Clipy 阅读")
    title = escape(title_text)
    source_name = escape(entry.get("sourceName") or entry.get("sourceKey") or "收藏")
    source_url = entry.get("url") or ""
    source_link = f'<a href="{escape(source_url)}">{escape(urlparse(source_url).hostname or source_url)}</a>' if source_url else ""
    saved_at = format_reader_time(entry.get("createdAt"))
    body = markdown_to_html(reader_markdown_body(markdown))
    ai_block = reader_ai_summary_block(entry)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <style>
    :root {{ color-scheme: light; }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; background: #f4f1eb; color: #1f272a; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", sans-serif; }}
    main {{ max-width: 820px; margin: 0 auto; padding: 42px 24px 80px; background: #fffdf9; min-height: 100vh; }}
    .reader-header {{ margin-bottom: 22px; padding-bottom: 18px; border-bottom: 1px solid #e4ddd2; }}
    .reader-source {{ margin: 0 0 12px; color: #66706f; font-size: 14px; }}
    .reader-source a {{ color: #176b65; text-decoration: none; }}
    .reader-title {{ margin: 0; color: #172123; font-size: 32px; line-height: 1.25; letter-spacing: 0; }}
    .reader-meta {{ display: flex; flex-wrap: wrap; gap: 8px 14px; margin-top: 12px; color: #78817f; font-size: 14px; }}
    .reader-content h1 {{ display: none; }}
    .reader-content h2 {{ margin-top: 34px; padding-top: 10px; color: #1f2c2e; font-size: 22px; line-height: 1.35; border-top: 1px solid #eee8dd; }}
    .reader-content h3 {{ margin-top: 26px; font-size: 18px; line-height: 1.4; }}
    .reader-content p, .reader-content li {{ color: #253033; font-size: 17px; line-height: 1.86; }}
    .reader-content p {{ margin: 0 0 14px; }}
    .reader-content ul {{ padding-left: 1.25rem; }}
    .reader-content img {{ display: block; max-width: 100%; height: auto; margin: 18px auto; border-radius: 8px; }}
    .reader-content a {{ color: #176b65; }}
    .reader-ai {{ margin: 24px 0 32px; padding: 18px 20px; border: 1px solid #cfe2dc; border-radius: 8px; background: #f4fbf8; color: #173f3d; }}
    .reader-ai h2 {{ margin: 0 0 10px; padding: 0; border: 0; font-size: 18px; }}
    .reader-ai p {{ margin: 0 0 10px; font-size: 15px; line-height: 1.75; color: #173f3d; }}
    .reader-ai ul {{ margin: 10px 0 0; padding-left: 1.25rem; }}
    .reader-ai li {{ font-size: 15px; line-height: 1.65; }}
    .reader-ai-tags {{ display: flex; flex-wrap: wrap; gap: 8px; margin-top: 12px; }}
    .reader-ai-tags span {{ padding: 4px 8px; border-radius: 999px; background: #dcefeb; font-size: 13px; }}
    @media (max-width: 640px) {{
      main {{ padding: 28px 18px 64px; }}
      .reader-title {{ font-size: 25px; }}
      .reader-content p, .reader-content li {{ font-size: 16px; line-height: 1.8; }}
    }}
  </style>
</head>
<body>
  <main>
    <header class="reader-header">
      <p class="reader-source">{source_name}{f" · {source_link}" if source_link else ""}</p>
      <h1 class="reader-title">{title}</h1>
      <div class="reader-meta">{f"<span>保存于 {escape(saved_at)}</span>" if saved_at else ""}</div>
    </header>
    {ai_block}
    <article class="reader-content">
      {body}
    </article>
  </main>
</body>
</html>"""


def reader_markdown_body(markdown):
    body = strip_front_matter(markdown).strip()
    match = re.search(r"(?m)^##\s+正文\s*$", body)
    if match:
        return body[match.end() :].strip()
    lines = body.splitlines()
    while lines and (not lines[0].strip() or lines[0].startswith("# ") or lines[0].startswith("- 来源：") or lines[0].startswith("- 原链接：") or lines[0].startswith("- 保存时间：")):
        lines.pop(0)
    return "\n".join(lines).strip()


def format_reader_time(value):
    try:
        timestamp = int(value or 0) / 1000
        if timestamp <= 0:
            return ""
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(timestamp))
    except Exception:
        return ""


def reader_ai_summary_block(entry):
    ai = entry.get("ai") or {}
    ai_status = entry.get("aiStatus") or ai.get("status") or ""
    summary = clean_inline_text(ai.get("summary") or "")
    key_points = clean_string_list(ai.get("keyPoints") or [])
    suggested_tags = clean_string_list(ai.get("suggestedTags") or [])
    if ai_status != "ready" and not summary and not key_points and not suggested_tags:
        if ai_status != "summarizing":
            return ""
        return """
<section class="reader-ai reader-ai-pending">
  <h2>AI 摘要</h2>
  <p>AI 正在总结。</p>
</section>
"""
    parts = ['<section class="reader-ai">', "<h2>AI 摘要</h2>"]
    if summary:
        parts.append(f"<p>{escape(summary)}</p>")
    if key_points:
        parts.append("<ul>")
        parts.extend(f"<li>{escape(point)}</li>" for point in key_points)
        parts.append("</ul>")
    if suggested_tags:
        tags = "".join(f"<span>{escape(tag)}</span>" for tag in suggested_tags)
        parts.append(f'<div class="reader-ai-tags">{tags}</div>')
    parts.append("</section>")
    return "\n".join(parts)


def inject_reader_ai_summary(entry, html):
    block = reader_ai_summary_block(entry)
    if not block:
        return html
    return html.replace("<main>", f"<main>\n{block}", 1)


def rewrite_reader_asset_links(entry_id, html):
    prefix = f"/api/reader-assets/{quote(entry_id)}/"
    html = re.sub(r'(<img\b[^>]*\bsrc=")assets/([^"]+)"', lambda match: f"{match.group(1)}{prefix}{quote(match.group(2))}\"", html)
    html = re.sub(r"(<img\b[^>]*\bsrc=')assets/([^']+)'", lambda match: f"{match.group(1)}{prefix}{quote(match.group(2))}'", html)
    return html


def open_with_system(target):
    if sys.platform == "darwin":
        command = ["open", str(target)]
    elif sys.platform.startswith("win"):
        command = ["cmd", "/c", "start", "", str(target)]
    else:
        command = ["xdg-open", str(target)]
    subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def detect_source_key(url):
    host = (urlparse(url).hostname or "").removeprefix("www.").lower()
    if "xiaohongshu.com" in host or "xhslink.com" in host:
        return "xhs"
    if "dianping.com" in host or host == "dpurl.cn" or host.endswith(".dpurl.cn"):
        return "dianping"
    if "zhihu.com" in host:
        return "zhihu"
    if host == "x.com" or host.endswith(".x.com") or "twitter.com" in host:
        return "x"
    if host == "youtube.com" or host.endswith(".youtube.com") or host == "youtu.be" or "youtube-nocookie.com" in host:
        return "youtube"
    if host == "bilibili.com" or host.endswith(".bilibili.com") or host == "b23.tv":
        return "bilibili"
    if host == "mp.weixin.qq.com":
        return "wechat"
    if "weibo.com" in host or "weibo.cn" in host:
        return "weibo"
    if host == "github.com" or host.endswith(".github.com"):
        return "github"
    if host == "wsj.com" or host.endswith(".wsj.com"):
        return "wsj"
    if host == "ft.com" or host.endswith(".ft.com"):
        return "ft"
    if host == "reddit.com" or host.endswith(".reddit.com"):
        return "reddit"
    return "web"


def get_logo_file_for_url(raw_url):
    parsed = urlparse(raw_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None

    host = parsed.hostname.lower()
    cached = find_cached_logo(host)
    if cached:
        return cached

    for logo_url in build_logo_candidates(parsed):
        cached = fetch_and_cache_logo(logo_url, host)
        if cached:
            return cached
    return None


def find_cached_logo(host):
    for path in LOGOS_DIR.glob(f"{safe_logo_stem(host)}.*"):
        if path.is_file():
            return path
    return None


def safe_logo_stem(host):
    digest = hashlib.sha1(host.encode("utf-8")).hexdigest()[:8]
    clean_host = re.sub(r"[^a-z0-9.-]+", "-", host.lower()).strip(".-") or "site"
    return f"{clean_host}-{digest}"


def build_logo_candidates(parsed_url):
    origin = f"{parsed_url.scheme}://{parsed_url.netloc}"
    candidates = [
        urljoin(origin, "/favicon.ico"),
        urljoin(origin, "/favicon.png"),
        urljoin(origin, "/apple-touch-icon.png"),
        urljoin(origin, "/apple-touch-icon-precomposed.png"),
    ]

    html = fetch_homepage_html(origin)
    if html:
        candidates = extract_icon_links(html, origin) + candidates
    return list(dict.fromkeys(candidates))


def fetch_homepage_html(origin):
    try:
        request = Request(
            origin,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            },
        )
        with urlopen(request, timeout=4) as response:
            content_type = response.headers.get("Content-Type", "")
            if "text/html" not in content_type and "application/xhtml" not in content_type:
                return ""
            body = response.read(MAX_METADATA_BYTES)
            if response.headers.get("Content-Encoding") == "gzip":
                body = gzip.decompress(body)
            return decode_html(body, content_type)
    except Exception:
        return ""


def extract_icon_links(html, base_url):
    links = []
    for match in re.finditer(r"<link\b[^>]*>", html, re.I):
        attrs = parse_tag_attrs(match.group(0))
        rel = attrs.get("rel", "").lower()
        href = attrs.get("href", "")
        if not href:
            continue
        if "icon" in rel or "apple-touch-icon" in rel or "mask-icon" in rel:
            links.append(urljoin(base_url, href))
    return links


def parse_tag_attrs(tag):
    attrs = {}
    for attr_match in re.finditer(r'([:\w-]+)\s*=\s*("([^"]*)"|\'([^\']*)\'|([^\s>]+))', tag, re.I):
        attr_value = attr_match.group(3) or attr_match.group(4) or attr_match.group(5) or ""
        attrs[attr_match.group(1).lower()] = unescape(attr_value)
    return attrs


def fetch_and_cache_logo(logo_url, host):
    try:
        request = Request(
            logo_url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
                ),
                "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
            },
        )
        with urlopen(request, timeout=5) as response:
            content_type = response.headers.get("Content-Type", "").split(";")[0].strip().lower()
            body = response.read(MAX_LOGO_BYTES + 1)
            if len(body) > MAX_LOGO_BYTES:
                return None
    except Exception:
        return None

    extension = logo_extension(content_type, logo_url)
    if not extension:
        return None

    path = LOGOS_DIR / f"{safe_logo_stem(host)}{extension}"
    path.write_bytes(body)
    return path


def logo_extension(content_type, logo_url):
    by_type = {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/gif": ".gif",
        "image/webp": ".webp",
        "image/svg+xml": ".svg",
        "image/x-icon": ".ico",
        "image/vnd.microsoft.icon": ".ico",
    }
    if content_type in by_type:
        return by_type[content_type]

    path = urlparse(logo_url).path.lower()
    for extension in [".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico"]:
        if path.endswith(extension):
            return ".jpg" if extension == ".jpeg" else extension
    return ""


def normalize_entry_source(entry):
    if entry.get("kind") != "link" or not entry.get("url"):
        return entry

    source_key = detect_source_key(entry["url"])
    if entry.get("sourceKey") != source_key:
        entry = dict(entry)
        entry["sourceKey"] = source_key
        entry["sourceName"] = SOURCE_NAMES.get(source_key, "网页")
    return entry


def fetch_page_title(url, timeout=8):
    source_key = detect_source_key(url)
    platform_title = fetch_platform_title(url, source_key)
    if platform_title:
        return platform_title

    try:
        request = Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Accept-Encoding": "identity",
            },
        )
        with urlopen(request, timeout=timeout) as response:
            content_type = response.headers.get("Content-Type", "")
            if "text/html" not in content_type and "application/xhtml" not in content_type:
                return title_from_url_slug(url)
            body = response.read(MAX_METADATA_BYTES)
            if response.headers.get("Content-Encoding") == "gzip":
                body = gzip.decompress(body)
    except Exception:
        return title_from_url_slug(url)

    text = decode_html(body, content_type)
    title = (
        find_meta_content(text, "property", "og:title")
        or find_meta_content(text, "name", "twitter:title")
        or find_meta_content(text, "itemprop", "name")
        or find_tag_title(text)
    )
    return clean_page_title(title, url) or title_from_url_slug(url)


def title_from_url_slug(url):
    path_parts = [part for part in urlparse(url).path.split("/") if part]
    ignored = {
        "news",
        "articles",
        "article",
        "markets",
        "technology",
        "business",
        "world",
        "opinion",
        "features",
    }
    for part in reversed(path_parts):
        decoded = unquote(part).strip("/")
        lower = decoded.lower()
        if lower in ignored:
            continue
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}|\d+", lower):
            continue
        if "-" not in decoded or not re.search(r"[A-Za-z\u4e00-\u9fff]", decoded):
            continue
        words = [word for word in re.split(r"[-_]+", decoded) if word]
        if len(words) < 3:
            continue
        return headline_case(words)[:120]
    return ""


def headline_case(words):
    small_words = {"a", "an", "and", "as", "at", "but", "by", "for", "from", "in", "nor", "of", "on", "or", "per", "the", "to", "vs", "via", "with"}
    cased = []
    for index, word in enumerate(words):
        lower = word.lower()
        if index > 0 and lower in small_words:
            cased.append(lower)
        elif word.isupper():
            cased.append(word)
        else:
            cased.append(lower[:1].upper() + lower[1:])
    return " ".join(cased)


def fetch_json(url, timeout=6, headers=None):
    request_headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        ),
        "Accept": "application/json,text/plain,*/*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    request_headers.update(headers or {})
    request = Request(url, headers=request_headers)
    with urlopen(request, timeout=timeout) as response:
        body = response.read(MAX_METADATA_BYTES)
        if response.headers.get("Content-Encoding") == "gzip":
            body = gzip.decompress(body)
        return json.loads(decode_html(body, response.headers.get("Content-Type", "")))


def fetch_platform_title(url, source_key):
    if source_key == "bilibili":
        return fetch_bilibili_title(url)
    if source_key == "x":
        return fetch_x_title(url)
    if source_key == "weibo":
        return fetch_weibo_title(url)
    if source_key == "github":
        return fetch_github_title(url)
    return ""


def fetch_bilibili_title(url):
    bvid_match = re.search(r"(BV[0-9A-Za-z]+)", url)
    if not bvid_match:
        return ""

    try:
        data = fetch_json(f"https://api.bilibili.com/x/web-interface/view?bvid={bvid_match.group(1)}", timeout=6)
    except Exception:
        return ""

    video = data.get("data") or {}
    title = clean_inline_text(video.get("title") or "")
    return title[:120]


def fetch_x_title(url):
    parsed = urlparse(url)
    path_parts = [part for part in parsed.path.split("/") if part]
    if len(path_parts) < 3 or path_parts[1] != "status":
        if path_parts:
            return f"@{path_parts[0]} 的主页"
        return ""

    username = path_parts[0]
    status_id = path_parts[2]

    fxtwitter_title = fetch_x_fxtwitter_title(username, status_id)
    if fxtwitter_title:
        return fxtwitter_title

    oembed_title = fetch_x_oembed_title(url, username)
    if oembed_title:
        return oembed_title

    for endpoint in [
        f"https://cdn.syndication.twimg.com/tweet-result?id={quote(status_id)}&lang=zh-cn",
        f"https://cdn.syndication.twimg.com/tweet-result?id={quote(status_id)}&lang=en",
        f"https://cdn.syndication.twimg.com/tweet-result?id={quote(status_id)}&lang=zh-cn&token=a",
        f"https://cdn.syndication.twimg.com/tweet-result?id={quote(status_id)}&lang=en&token=a",
    ]:
        try:
            data = fetch_json(endpoint, timeout=5)
            title = format_x_tweet(data, username)
            if title:
                return title
        except Exception:
            continue

    return f"@{username} 的帖子"


def fetch_x_fxtwitter_title(username, status_id):
    try:
        data = fetch_json(f"https://api.fxtwitter.com/{quote(username)}/status/{quote(status_id)}", timeout=6)
    except Exception:
        return ""

    tweet = data.get("tweet") if isinstance(data, dict) else {}
    if not isinstance(tweet, dict):
        return ""

    article = tweet.get("article") if isinstance(tweet.get("article"), dict) else {}
    text = (
        article.get("title")
        or article.get("preview_text")
        or x_text_value(tweet.get("text"))
        or x_text_value(tweet.get("raw_text"))
    )
    text = clean_inline_text(text)
    text = re.sub(r"^https?://\\S+$", "", text).strip()
    if not text:
        return ""

    author = tweet.get("author") if isinstance(tweet.get("author"), dict) else {}
    screen_name = author.get("screen_name") or username
    return f"@{screen_name}：{shorten_text(text, 46)}"


def x_text_value(value):
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return value.get("text") or ""
    return ""


def fetch_x_oembed_title(url, fallback_username):
    try:
        data = fetch_json(f"https://publish.twitter.com/oembed?url={quote(url, safe='')}", timeout=6)
    except Exception:
        return ""
    return format_x_oembed(data, fallback_username)


def format_x_oembed(data, fallback_username):
    if not isinstance(data, dict):
        return ""

    html = data.get("html") or ""
    paragraph_match = re.search(r"<p\b[^>]*>(.*?)</p>", html, re.I | re.S)
    text_html = paragraph_match.group(1) if paragraph_match else html
    text = clean_inline_text(strip_html(re.sub(r"<br\s*/?>", " ", text_html, flags=re.I)))
    if not text:
        return ""
    return f"@{fallback_username}：{shorten_text(text, 46)}"


def format_x_tweet(data, fallback_username):
    if not isinstance(data, dict):
        return ""

    text = clean_inline_text(data.get("text") or data.get("full_text") or "")
    user = data.get("user") or {}
    username = (
        user.get("screen_name")
        or user.get("username")
        or user.get("name")
        or data.get("screen_name")
        or fallback_username
    )

    if not text:
        return ""
    return f"@{username}：{shorten_text(text, 46)}"


def fetch_weibo_title(url):
    parsed = urlparse(url)
    path_parts = [part for part in parsed.path.split("/") if part]
    status_id = ""
    if path_parts:
        status_id = path_parts[-1]
    query_id = parse_qs(parsed.query).get("id", [""])[0]
    status_id = query_id or status_id
    if not status_id:
        return ""

    endpoints = [
        f"https://weibo.com/ajax/statuses/show?id={quote(status_id)}",
        f"https://m.weibo.cn/statuses/show?id={quote(status_id)}",
    ]
    for endpoint in endpoints:
        try:
            data = fetch_json(
                endpoint,
                timeout=5,
                headers={
                    "Referer": "https://weibo.com/",
                    "X-Requested-With": "XMLHttpRequest",
                },
            )
            title = format_weibo_status(data)
            if title:
                return title
        except Exception:
            continue

    if len(path_parts) >= 2:
        return f"微博用户 {path_parts[0]} 的帖子"
    return "微博帖子"


def fetch_github_title(url):
    repo = parse_github_repo(url)
    if not repo:
        return ""
    owner, repo_name = repo

    try:
        data = fetch_json(f"https://api.github.com/repos/{quote(owner)}/{quote(repo_name)}", timeout=6)
    except Exception:
        data = {}

    full_name = data.get("full_name") or f"{owner}/{repo_name}"
    description = clean_inline_text(data.get("description") or "")
    if description:
        return f"{full_name}：{shorten_text(description, 72)}"
    return full_name


def parse_github_repo(url):
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if host != "github.com" and not host.endswith(".github.com"):
        return None
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 2:
        return None
    owner, repo_name = parts[0], parts[1]
    reserved = {
        "about",
        "blog",
        "collections",
        "enterprise",
        "explore",
        "features",
        "login",
        "marketplace",
        "new",
        "pricing",
        "search",
        "settings",
        "signup",
        "topics",
        "trending",
    }
    if owner.lower() in reserved:
        return None
    return owner, repo_name


def format_weibo_status(data):
    if not isinstance(data, dict) or data.get("error") == "Forbidden":
        return ""

    status = data.get("data") if isinstance(data.get("data"), dict) else data
    text = status.get("text_raw") or status.get("text") or status.get("longTextContent") or ""
    user = status.get("user") or {}
    username = user.get("screen_name") or user.get("name") or ""
    text = clean_inline_text(strip_html(text))
    if not text:
        return ""
    prefix = username or "微博"
    return f"{prefix}：{shorten_text(text, 46)}"


def strip_html(value):
    return re.sub(r"<[^>]+>", "", value or "")


def clean_inline_text(value):
    value = unescape(value or "")
    value = re.sub(r"https?://\S+", "", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def shorten_text(value, max_length):
    value = clean_inline_text(value)
    if len(value) <= max_length:
        return value
    return f"{value[: max_length - 1]}…"


def decode_html(body, content_type):
    charset_match = re.search(r"charset=([\w.-]+)", content_type, re.I)
    encodings = [charset_match.group(1)] if charset_match else []
    encodings.extend(["utf-8", "gb18030", "big5", "latin-1"])

    for encoding in encodings:
        try:
            return body.decode(encoding, errors="replace")
        except LookupError:
            continue
    return body.decode("utf-8", errors="replace")


def find_meta_content(html, key, value):
    pattern = re.compile(r"<meta\b[^>]*>", re.I)
    for match in pattern.finditer(html):
        tag = match.group(0)
        attrs = {}
        for attr_match in re.finditer(r'([:\w-]+)\s*=\s*("([^"]*)"|\'([^\']*)\'|([^\s>]+))', tag, re.I):
            attr_value = attr_match.group(3) or attr_match.group(4) or attr_match.group(5) or ""
            attrs[attr_match.group(1).lower()] = unescape(attr_value)
        actual = attrs.get(key.lower(), "").lower()
        if actual == value.lower() and attrs.get("content"):
            return attrs["content"]
    return ""


def find_tag_title(html):
    match = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
    if not match:
        return ""
    return match.group(1)


def clean_page_title(title, url):
    title = unescape(re.sub(r"\s+", " ", title or "")).strip()
    if not title:
        return ""

    host = urlparse(url).hostname or ""
    suffixes = [
        " - YouTube",
        "_哔哩哔哩_bilibili",
        "-哔哩哔哩_Bilibili",
        "- 哔哩哔哩",
        " | 微信公众平台",
        " - 微信公众平台",
        " | WeChat Official Account",
        " / X",
        " on X",
        " - 知乎",
        " - 小红书",
        "的微博_微博",
    ]
    for suffix in suffixes:
        if title.endswith(suffix):
            title = title[: -len(suffix)].strip()

    title = re.sub(r"^\(\d+\)\s*", "", title)
    blocked_titles = {
        "x",
        "微博",
        "weibo.com",
        "sina visitor system",
        "新浪通行证",
        "微信公众平台",
        "哔哩哔哩 (゜-゜)つロ 干杯~-bilibili",
        "bloomberg - are you a robot?",
        "are you a robot?",
        "access denied",
        "安全限制",
    }
    if title.lower() in blocked_titles or title.lower() == host.lower():
        return ""
    if host == "github.com" or host.endswith(".github.com"):
        title = re.sub(r"^GitHub\s*-\s*", "", title, flags=re.I)
        title = re.sub(r"\s*·\s*GitHub\s*$", "", title, flags=re.I).strip()
    return title[:120]


def is_generic_saved_title(entry):
    title = (entry.get("title") or "").strip()
    source = entry.get("sourceKey") or ""
    generic_titles = {
        "",
        "X 收藏",
        "Sina Visitor System",
        "YouTube 视频",
        "YouTube 收藏",
        "Bilibili 视频",
        "公众号文章",
        "GitHub 仓库",
        "大众点评收藏",
        "小红书收藏",
        "微博收藏",
        "网页",
    }
    if title in generic_titles:
        return True
    if source == "zhihu" and re.fullmatch(r"知乎(问题|文章)\s+\d+", title):
        return True
    if source == "x" and re.fullmatch(r"@[\w.-]+\s+的帖子", title):
        return True
    if source == "weibo" and re.fullmatch(r"微博\s+\d+", title):
        return True
    if " / " in title and not re.search(r"[\u4e00-\u9fff]", title):
        return True
    if title.startswith("mp.weixin.qq.com /"):
        return True
    if title.startswith("bilibili.com /"):
        return True
    return False


def drop_blocked_metadata_title(entry):
    title = clean_inline_text(entry.get("metadataTitle") or "")
    if title in {"安全限制", "小红书 - 安全验证", "Access Denied", "Are you a robot?"}:
        entry = dict(entry)
        entry.pop("metadataTitle", None)
        entry.pop("metadataFetchedAt", None)
        entry.pop("metadataSource", None)
    return entry


def enrich_link_entry(entry):
    entry = drop_blocked_metadata_title(normalize_entry_source(entry))
    url = entry.get("url")
    if not url or not is_generic_saved_title(entry):
        return entry

    title = fetch_page_title(url)
    if title:
        entry["title"] = title
        entry["metadataTitle"] = title
        entry["metadataFetchedAt"] = int(time.time() * 1000)
        entry.pop("metadataFetchFailedAt", None)
    else:
        entry["metadataFetchFailedAt"] = int(time.time() * 1000)
    return entry


def prepare_link_entry_for_initial_save(entry):
    entry = drop_blocked_metadata_title(normalize_entry_source(dict(entry)))
    if entry.get("kind") != "link" or not entry.get("url"):
        return entry
    if not is_generic_saved_title(entry):
        return entry

    title = fetch_initial_link_title(entry)
    if title:
        entry["title"] = title
        entry["metadataTitle"] = title
        entry["metadataFetchedAt"] = int(time.time() * 1000)
        entry["metadataSource"] = "initial"
        entry.pop("metadataFetchFailedAt", None)
    return entry


def fetch_initial_link_title(entry):
    url = entry.get("url") or ""
    source_key = entry.get("sourceKey") or detect_source_key(url)

    title = ""
    if source_key in {"github", "bilibili", "x", "weibo"}:
        title = fetch_platform_title(url, source_key)

    if not title and source_key in {"github", "wechat", "zhihu", "web", "wsj", "ft", "reddit"}:
        title = fetch_page_title(url, timeout=4)

    return clean_page_title(title, url) or title_from_url_slug(url)


def should_try_metadata(entry):
    if entry.get("kind") != "link" or not entry.get("url"):
        return False
    entry = normalize_entry_source(dict(entry))
    if not is_generic_saved_title(entry):
        return False
    if entry.get("sourceKey") in {"bilibili", "x", "weibo"}:
        return True
    last_failed_at = int(entry.get("metadataFetchFailedAt") or 0)
    if last_failed_at and int(time.time() * 1000) - last_failed_at < 24 * 60 * 60 * 1000:
        return False
    return True


def enrich_existing_links(entries):
    changed = False
    enriched_entries = []
    attempts = 0

    for entry in entries:
        if attempts < 5 and should_try_metadata(entry):
            before = entry.get("title")
            entry = enrich_link_entry(dict(entry))
            attempts += 1
            changed = changed or entry.get("title") != before or entry.get("metadataFetchFailedAt")
        enriched_entries.append(entry)

    if changed:
        write_entries(enriched_entries)
    return enriched_entries


def start_background_entry_processing(entry, force_refresh=False):
    worker = threading.Thread(target=process_entry_in_background, args=(dict(entry), force_refresh), daemon=True)
    worker.start()


def mark_entry_processing(entry, status, step="", error=""):
    updated = dict(entry)
    updated["processingStatus"] = status
    if step:
        updated["processingStep"] = step
    else:
        updated.pop("processingStep", None)
    if error:
        updated["processingError"] = error[:200]
    else:
        updated.pop("processingError", None)
    upsert_entry(updated)
    return updated


def process_entry_in_background(entry, force_refresh=False):
    updated = dict(entry)
    try:
        if updated.get("kind") == "link":
            updated = mark_entry_processing(updated, "fetching", "fetching")
            updated = process_link_with_browser_profile(updated, force_refresh=force_refresh)
            updated = mark_entry_processing(updated, "parsing", "parsing")
        else:
            updated = mark_entry_processing(updated, "parsing", "parsing")

        updated = mark_entry_processing(updated, "archiving", "archiving")
        updated = build_item_for_entry(updated, DATA_DIR)
        updated.pop("processingError", None)
        updated.pop("processingStep", None)
        updated["processingStatus"] = "ready"
        updated["processedAt"] = int(time.time() * 1000)
        upsert_entry(updated)
        start_background_ai_summary(updated)
    except Exception as error:
        updated["processingStatus"] = "failed"
        updated.pop("processingStep", None)
        updated["processingError"] = str(error)[:200]
        try:
            updated = build_item_for_entry(updated, DATA_DIR)
        except Exception:
            pass
        upsert_entry(updated)


def process_link_with_browser_profile(
    entry,
    allow_visible_browser=False,
    prefer_existing_browser_tab=False,
    force_refresh=False,
    require_existing_browser=False,
    require_existing_browser_tab=False,
):
    entry = prepare_link_entry_for_initial_save(entry)

    markdown_entry = capture_markdown_for_entry(entry, DATA_DIR, force=force_refresh)
    markdown_status = (markdown_entry.get("content") or {}).get("status") or ""
    should_use_profile_page = allow_visible_browser and prefer_existing_browser_tab and force_refresh
    should_prefer_rendered_capture = (markdown_entry.get("sourceKey") or "") in {"xhs"}
    if markdown_status in {"ready", "skipped_video"} and not should_use_profile_page and not should_prefer_rendered_capture:
        markdown_entry.pop("browserCapture", None)
        return markdown_entry
    if markdown_status == "needs_login" and not allow_visible_browser:
        return markdown_entry

    browser_result = fetch_with_browser_profile(
        entry.get("url"),
        DATA_DIR,
        reuse_existing=allow_visible_browser,
        prefer_existing_tab=prefer_existing_browser_tab,
        require_existing_browser=require_existing_browser,
        require_existing_tab=require_existing_browser_tab,
    )
    browser_summary = summarize_browser_result(browser_result)
    markdown_entry["browserCapture"] = browser_summary

    if browser_result.get("status") == "failed":
        return markdown_entry

    if browser_result.get("status") == "ready":
        title = clean_page_title(
            browser_result.get("title") or browser_result.get("documentTitle") or "",
            browser_result.get("finalUrl") or entry.get("url") or "",
        )
        if title:
            markdown_entry["metadataTitle"] = title
            markdown_entry["metadataFetchedAt"] = int(time.time() * 1000)
            markdown_entry["metadataSource"] = "browser_profile"
            markdown_entry.pop("metadataFetchFailedAt", None)
            slug_title = title_from_url_slug(markdown_entry.get("url") or "")
            current_title = clean_inline_text(markdown_entry.get("title") or "")
            if (
                not markdown_entry.get("renamedAt")
                and (is_generic_saved_title(markdown_entry) or (slug_title and current_title.lower() == clean_inline_text(slug_title).lower()))
            ):
                markdown_entry["title"] = title

    if browser_result.get("finalUrl") and browser_result.get("finalUrl") != markdown_entry.get("url"):
        markdown_entry["resolvedUrl"] = browser_result.get("finalUrl")

    return capture_browser_result_for_entry(markdown_entry, DATA_DIR, browser_result)


def request_entry_verification(entry_id):
    entry = next((item for item in read_entries(enrich_links=False) if item.get("id") == entry_id), None)
    if not entry:
        return None, {"error": "Entry not found"}, 404
    if entry.get("kind") != "link" or not entry.get("url"):
        return None, {"error": "Entry is not a link"}, 400

    now = int(time.time() * 1000)
    previous = entry.get("verification") or {}
    verification = {
        "status": "waiting",
        "startedAt": now,
        "openedAt": now,
        "expiresAt": now + VERIFICATION_TIMEOUT_MS,
        "attempt": int(previous.get("attempt") or 0) + 1,
        "lastCheckedAt": previous.get("lastCheckedAt"),
        "lastStatus": previous.get("lastStatus") or "",
        "reason": "",
    }
    opened = open_visible_browser_profile(entry.get("url"), DATA_DIR)
    verification["openResult"] = {
        "ok": bool(opened.get("ok")),
        "method": opened.get("method"),
        "reusedBrowser": opened.get("reusedBrowser"),
        "stoppedHeadless": opened.get("stoppedHeadless") or [],
        "reason": opened.get("reason") or "",
        "openedUrl": opened.get("openedUrl") or entry.get("url"),
    }
    if not opened.get("ok"):
        verification["status"] = "open_failed"
        verification["reason"] = opened.get("reason") or "无法打开 Clipy 浏览器"

    updated = dict(entry)
    updated["verification"] = verification
    updated["processingStatus"] = "ready"
    upsert_entry(updated)

    if opened.get("ok"):
        start_verification_worker(updated)

    return updated, {"entry": updated, "verification": verification}, 200


def request_entry_profile_open(entry_id):
    entry = next((item for item in read_entries(enrich_links=False) if item.get("id") == entry_id), None)
    if not entry:
        return None, {"error": "Entry not found"}, 404
    if entry.get("kind") != "link" or not entry.get("url"):
        return None, {"error": "Entry is not a link"}, 400

    now = int(time.time() * 1000)
    previous = entry.get("profileOpen") or {}
    opened = open_visible_browser_profile(entry.get("url"), DATA_DIR)
    profile_open = {
        "status": "watching" if opened.get("ok") else "open_failed",
        "openedAt": now,
        "expiresAt": now + sum(PROFILE_OPEN_REFRESH_DELAYS) * 1000 + 30_000,
        "attempt": int(previous.get("attempt") or 0) + 1,
        "reason": opened.get("reason") or "",
        "openResult": {
            "ok": bool(opened.get("ok")),
            "method": opened.get("method"),
            "reusedBrowser": opened.get("reusedBrowser"),
            "stoppedHeadless": opened.get("stoppedHeadless") or [],
            "reason": opened.get("reason") or "",
            "openedUrl": opened.get("openedUrl") or entry.get("url"),
        },
    }

    updated = dict(entry)
    updated["profileOpen"] = profile_open
    upsert_entry(updated)

    if opened.get("ok"):
        start_profile_open_worker(updated)

    return updated, {"ok": bool(opened.get("ok")), "entry": updated, "profileOpen": profile_open}, 200 if opened.get("ok") else 500


def start_verification_worker(entry):
    entry_id = entry.get("id")
    if not entry_id:
        return
    with VERIFICATION_LOCK:
        if entry_id in VERIFICATION_WORKERS:
            return
        VERIFICATION_WORKERS.add(entry_id)
    worker = threading.Thread(target=verification_worker, args=(entry_id,), daemon=True)
    worker.start()


def start_profile_open_worker(entry):
    entry_id = entry.get("id")
    attempt = (entry.get("profileOpen") or {}).get("attempt")
    if not entry_id or not attempt:
        return
    worker_key = f"{entry_id}:{attempt}"
    with PROFILE_OPEN_LOCK:
        if worker_key in PROFILE_OPEN_WORKERS:
            return
        PROFILE_OPEN_WORKERS.add(worker_key)
    worker = threading.Thread(target=profile_open_worker, args=(entry_id, attempt, worker_key), daemon=True)
    worker.start()


def profile_open_worker(entry_id, attempt, worker_key):
    last_status = ""
    try:
        for delay_seconds in PROFILE_OPEN_REFRESH_DELAYS:
            time.sleep(delay_seconds)
            latest = next((item for item in read_entries(enrich_links=False) if item.get("id") == entry_id), None)
            if not latest:
                return
            profile_open = dict(latest.get("profileOpen") or {})
            if profile_open.get("attempt") != attempt:
                return

            updated = process_link_with_browser_profile(
                latest,
                allow_visible_browser=True,
                prefer_existing_browser_tab=True,
                force_refresh=True,
                require_existing_browser=True,
                require_existing_browser_tab=True,
            )
            current = next((item for item in read_entries(enrich_links=False) if item.get("id") == entry_id), None)
            if not current:
                return
            current_profile_open = dict(current.get("profileOpen") or {})
            if current_profile_open.get("attempt") != attempt:
                return
            if current.get("renamedAt"):
                updated["title"] = current.get("title")
                updated["renamedAt"] = current.get("renamedAt")

            content_status = (updated.get("content") or {}).get("status") or ""
            browser_status = (updated.get("browserCapture") or {}).get("status") or ""
            browser_closed = bool((updated.get("browserCapture") or {}).get("browserClosed") or (updated.get("browserCapture") or {}).get("existingTargetMissing"))
            last_status = content_status or browser_status
            current_profile_open.update(
                {
                    "status": "watching",
                    "lastCheckedAt": int(time.time() * 1000),
                    "lastStatus": last_status,
                }
            )
            if browser_closed:
                current_profile_open["status"] = "closed"
                current_profile_open["completedAt"] = current_profile_open["lastCheckedAt"]
                current_profile_open["reason"] = "Clipy 浏览器或当前标签页已关闭"
                updated["profileOpen"] = current_profile_open
                updated["processingStatus"] = "ready"
                try:
                    updated = build_item_for_entry(updated, DATA_DIR)
                except Exception:
                    pass
                upsert_entry(updated)
                return
            if content_status == "ready":
                current_profile_open["lastReadyAt"] = current_profile_open["lastCheckedAt"]
                current_profile_open["status"] = "complete"
                current_profile_open["completedAt"] = current_profile_open["lastCheckedAt"]
                updated["profileOpen"] = current_profile_open
                updated["processingStatus"] = "ready"
                updated.pop("processingError", None)
                try:
                    updated = build_item_for_entry(updated, DATA_DIR)
                except Exception:
                    pass
                upsert_entry(updated)
                return

            updated["profileOpen"] = current_profile_open
            updated["processingStatus"] = "ready"
            updated.pop("processingError", None)
            try:
                updated = build_item_for_entry(updated, DATA_DIR)
            except Exception:
                pass
            upsert_entry(updated)

        latest = next((item for item in read_entries(enrich_links=False) if item.get("id") == entry_id), None)
        if latest:
            profile_open = dict(latest.get("profileOpen") or {})
            if profile_open.get("attempt") == attempt:
                profile_open["status"] = "complete" if last_status == "ready" else "waiting_for_login"
                profile_open["completedAt"] = int(time.time() * 1000)
                latest = dict(latest)
                latest["profileOpen"] = profile_open
                upsert_entry(latest)
    finally:
        with PROFILE_OPEN_LOCK:
            PROFILE_OPEN_WORKERS.discard(worker_key)


def verification_worker(entry_id):
    try:
        while True:
            entry = next((item for item in read_entries(enrich_links=False) if item.get("id") == entry_id), None)
            if not entry:
                return
            verification = dict(entry.get("verification") or {})
            if verification.get("status") != "waiting":
                return
            now = int(time.time() * 1000)
            if now > int(verification.get("expiresAt") or 0):
                verification["status"] = "expired"
                verification["lastCheckedAt"] = now
                verification["reason"] = "验证未完成，可继续"
                entry = dict(entry)
                entry["verification"] = verification
                upsert_entry(entry)
                return

            time.sleep(VERIFICATION_POLL_SECONDS)
            entry = next((item for item in read_entries(enrich_links=False) if item.get("id") == entry_id), None)
            if not entry:
                return
            verification = dict(entry.get("verification") or {})
            if verification.get("status") != "waiting":
                return

            updated = process_link_with_browser_profile(
                entry,
                allow_visible_browser=True,
                prefer_existing_browser_tab=True,
                force_refresh=True,
                require_existing_browser=True,
                require_existing_browser_tab=True,
            )
            latest = next((item for item in read_entries(enrich_links=False) if item.get("id") == entry_id), None)
            if not latest:
                return
            latest_verification = latest.get("verification") or {}
            if latest_verification.get("status") != "waiting" or latest_verification.get("attempt") != verification.get("attempt"):
                return
            if latest.get("renamedAt"):
                updated["title"] = latest.get("title")
                updated["renamedAt"] = latest.get("renamedAt")

            content_status = (updated.get("content") or {}).get("status") or ""
            browser_status = (updated.get("browserCapture") or {}).get("status") or content_status
            browser_closed = bool((updated.get("browserCapture") or {}).get("browserClosed") or (updated.get("browserCapture") or {}).get("existingTargetMissing"))
            now = int(time.time() * 1000)
            verification.update(
                {
                    "lastCheckedAt": now,
                    "lastStatus": browser_status,
                    "lastTextLength": (updated.get("browserCapture") or {}).get("textLength", 0),
                    "lastImageCount": (updated.get("browserCapture") or {}).get("imageCount", 0),
                }
            )

            if browser_closed:
                verification["status"] = "expired"
                verification["reason"] = "Clipy 浏览器或当前标签页已关闭，可点击继续"
                verification["completedAt"] = now
                updated["verification"] = verification
                updated["processingStatus"] = "ready"
                try:
                    updated = build_item_for_entry(updated, DATA_DIR)
                except Exception:
                    pass
                upsert_entry(updated)
                return

            if content_status == "ready":
                verification["status"] = "complete"
                verification["completedAt"] = now
                updated["verification"] = verification
                updated["processingStatus"] = "ready"
                updated.pop("processingError", None)
                updated = build_item_for_entry(updated, DATA_DIR)
                upsert_entry(updated)
                return

            verification["status"] = "waiting"
            updated["verification"] = verification
            updated["processingStatus"] = "ready"
            try:
                updated = build_item_for_entry(updated, DATA_DIR)
            except Exception:
                pass
            upsert_entry(updated)
    finally:
        with VERIFICATION_LOCK:
            VERIFICATION_WORKERS.discard(entry_id)


class ClipyHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def log_message(self, format, *args):
        return

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PATCH, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Clipy-Id, X-Clipy-File-Id, X-File-Name, X-File-Type")
        self.end_headers()

    def handle_clerk_unavailable(self, path):
        message = "Clerk module unavailable. Add `clerk/clerk_app` to enable /clerk features."
        if path.startswith("/clerk/api/"):
            json_response(
                self,
                {
                    "error": "clerk unavailable",
                    "message": message,
                    "importError": CLERK_IMPORT_ERROR,
                },
                503,
            )
            return
        html_response(self, f"<h1>Clerk unavailable</h1><p>{message}</p>", 503)

    def do_GET(self):
        path = urlparse(self.path).path
        if path.startswith("/clerk") and not CLERK_AVAILABLE:
            self.handle_clerk_unavailable(path)
            return

        if path in {"/clerk", "/clerk/"}:
            html_response(self, CLERK_HTML)
            return

        if path in {"/clerk/wiki", "/clerk/wiki/"}:
            html_response(self, CLERK_WIKI_HTML)
            return

        if path == "/clerk/api/status":
            config = clerk_llm_config()
            json_response(
                self,
                {
                    "llmConfigured": bool(config.get("api_key")),
                    "baseUrl": config.get("base_url"),
                    "model": config.get("model"),
                    "defaultBaseUrl": CLERK_DEFAULT_BASE_URL,
                    "defaultModel": CLERK_DEFAULT_MODEL,
                    "knowledgeBase": clerk_wiki_status(),
                },
            )
            return

        if path == "/clerk/api/wiki":
            json_response(self, {"markdown": clerk_read_main_wiki(), "sourceIndex": clerk_source_index(), "knowledgeBase": clerk_wiki_status()})
            return

        if path == "/clerk/api/sessions":
            json_response(self, {"sessions": clerk_list_sessions()})
            return

        if path.startswith("/clerk/api/sessions/"):
            session_id = path.removeprefix("/clerk/api/sessions/")
            session = clerk_read_session(session_id)
            if session:
                json_response(self, {"session": session})
            else:
                json_response(self, {"error": "session not found"}, 404)
            return

        if path == "/clerk/api/articles":
            json_response(self, {"articles": clerk_list_articles()})
            return

        if path == "/clerk/api/search":
            query = parse_qs(urlparse(self.path).query).get("q", [""])[0]
            json_response(self, clerk_search(query))
            return

        if path == "/api/entries":
            json_response(self, {"entries": read_entries(enrich_links=False), "repositories": read_repositories(), "storagePath": str(DATA_DIR)})
            return

        if path == "/api/repositories":
            json_response(self, {"repositories": read_repositories()})
            return

        if path == "/api/search":
            query = parse_qs(urlparse(self.path).query).get("q", [""])[0]
            json_response(self, {"query": clean_search_query(query), "results": search_entries(query)})
            return

        if path == "/api/ai/status":
            json_response(self, public_ai_config())
            return

        if path == "/api/browser-sessions":
            json_response(self, {"sessions": list_browser_sessions()})
            return

        if path == "/api/logo":
            self.serve_logo(parse_qs(urlparse(self.path).query).get("url", [""])[0])
            return

        if path.startswith("/api/reader-assets/"):
            self.serve_reader_asset(path.removeprefix("/api/reader-assets/"))
            return

        if path.startswith("/api/reader/"):
            self.serve_reader(path.removeprefix("/api/reader/"))
            return

        if path.startswith("/api/files/"):
            self.serve_file(path.removeprefix("/api/files/"))
            return

        super().do_GET()

    def do_POST(self):
        path = urlparse(self.path).path
        if path.startswith("/clerk") and not CLERK_AVAILABLE:
            self.handle_clerk_unavailable(path)
            return

        if path == "/clerk/api/sessions":
            json_response(self, {"session": clerk_create_session()})
            return

        if path == "/clerk/api/ingest":
            try:
                payload = json.loads(self.read_body().decode("utf-8"))
            except json.JSONDecodeError:
                payload = {}
            result = clerk_ingest_incremental(
                force=bool(payload.get("force")),
                use_llm=payload.get("useLlm", True) is not False,
                require_llm=bool(payload.get("requireLlm")),
            )
            result.pop("manifest", None)
            json_response(self, result)
            return

        if path == "/clerk/api/ask":
            try:
                payload = json.loads(self.read_body().decode("utf-8"))
            except json.JSONDecodeError:
                payload = {}
            session_id = payload.get("sessionId") or clerk_create_session().get("id")
            clerk_append_message(session_id, "user", payload.get("question", ""))
            result = clerk_answer(
                payload.get("question", ""),
                overrides={
                    "apiKey": payload.get("apiKey", ""),
                    "baseUrl": payload.get("baseUrl", ""),
                    "model": payload.get("model", ""),
                },
            )
            clerk_append_message(session_id, "assistant", result.get("answer", ""), sources=result.get("sources", []), usage=result.get("usage"))
            result["sessionId"] = session_id
            json_response(self, result)
            return

        if path == "/api/entries/link":
            self.save_link()
            return

        if path == "/api/entries/file":
            self.save_file()
            return

        if path == "/api/repositories":
            self.create_repository()
            return

        if path == "/api/browser-sessions/logout":
            self.logout_browser_session()
            return

        if path == "/api/browser-sessions/login":
            self.login_browser_session()
            return

        verify_match = re.fullmatch(r"/api/entries/([^/]+)/verify", path)
        if verify_match:
            self.verify_entry(unquote(verify_match.group(1)))
            return

        reparse_match = re.fullmatch(r"/api/entries/([^/]+)/reparse", path)
        if reparse_match:
            self.reparse_entry(unquote(reparse_match.group(1)))
            return

        summarize_match = re.fullmatch(r"/api/entries/([^/]+)/summarize", path)
        if summarize_match:
            self.summarize_entry(unquote(summarize_match.group(1)))
            return

        if path.startswith("/api/open/"):
            self.open_entry(path.removeprefix("/api/open/"))
            return

        text_response(self, "Not found", 404)

    def do_PATCH(self):
        path = urlparse(self.path).path
        if path.startswith("/api/repositories/"):
            self.update_repository(path.removeprefix("/api/repositories/"))
            return

        if path.startswith("/api/entries/"):
            self.update_entry(path.removeprefix("/api/entries/"))
            return

        text_response(self, "Not found", 404)

    def do_DELETE(self):
        path = urlparse(self.path).path
        if path.startswith("/api/repositories/"):
            self.delete_repository(path.removeprefix("/api/repositories/"))
            return

        batch_file_match = re.fullmatch(r"/api/entries/([^/]+)/files/([^/]+)", path)
        if batch_file_match:
            entry_id = unquote(batch_file_match.group(1))
            file_id = unquote(batch_file_match.group(2))
            entry, removed_file = remove_batch_file(entry_id, file_id)
            json_response(self, {"ok": bool(removed_file), "entry": entry, "removedFile": removed_file})
            return

        if path.startswith("/api/entries/"):
            entry_id = unquote(path.removeprefix("/api/entries/"))
            removed = remove_entry(entry_id)
            json_response(self, {"ok": removed})
            return

        text_response(self, "Not found", 404)

    def read_body(self):
        length = int(self.headers.get("Content-Length", "0") or "0")
        return self.rfile.read(length)

    def save_link(self):
        try:
            entry = json.loads(self.read_body().decode("utf-8"))
        except json.JSONDecodeError:
            json_response(self, {"error": "Bad JSON"}, 400)
            return

        if entry.get("kind") != "link" or not entry.get("url"):
            json_response(self, {"error": "Missing link data"}, 400)
            return

        entry.setdefault("createdAt", int(time.time() * 1000))
        entry = normalize_entry_source(entry)
        entry["processingStatus"] = "queued"
        entry["processingStep"] = "queued"
        entry.pop("processingError", None)
        upsert_entry(entry)
        start_background_entry_processing(entry)
        json_response(self, {"entry": entry})

    def update_entry(self, raw_id):
        try:
            payload = json.loads(self.read_body().decode("utf-8"))
        except json.JSONDecodeError:
            json_response(self, {"error": "Bad JSON"}, 400)
            return

        updated = update_entry(unquote(raw_id), payload)
        if not updated:
            json_response(self, {"error": "Entry not found or title is empty"}, 404)
            return

        updated = build_item_for_entry(updated, DATA_DIR)
        upsert_entry(updated)
        json_response(self, {"entry": updated})

    def create_repository(self):
        try:
            payload = json.loads(self.read_body().decode("utf-8"))
        except json.JSONDecodeError:
            payload = {}
        repo = create_repository(payload)
        json_response(self, {"repository": repo, "repositories": read_repositories()})

    def update_repository(self, raw_id):
        try:
            payload = json.loads(self.read_body().decode("utf-8"))
        except json.JSONDecodeError:
            json_response(self, {"error": "Bad JSON"}, 400)
            return

        updated = update_repository(unquote(raw_id), payload)
        if not updated:
            json_response(self, {"error": "Repository not found or name is empty"}, 404)
            return
        json_response(self, {"repository": updated, "repositories": read_repositories()})

    def delete_repository(self, raw_id):
        removed = delete_repository(unquote(raw_id))
        json_response(self, {"ok": removed, "repositories": read_repositories()}, 200 if removed else 404)

    def verify_entry(self, entry_id):
        _entry, payload, status = request_entry_verification(entry_id)
        json_response(self, payload, status)

    def reparse_entry(self, entry_id):
        entry = next((item for item in read_entries(enrich_links=False) if item.get("id") == entry_id), None)
        if not entry:
            json_response(self, {"error": "Entry not found"}, 404)
            return

        entry = dict(entry)
        entry["processingStatus"] = "queued"
        entry["processingStep"] = "queued"
        entry.pop("processingError", None)
        upsert_entry(entry)
        start_background_entry_processing(entry, force_refresh=True)
        json_response(self, {"entry": entry})

    def summarize_entry(self, entry_id):
        _entry, payload, status = request_entry_ai_summary(entry_id)
        json_response(self, payload, status)

    def open_profile_entry(self, entry_id):
        _entry, payload, status = request_entry_profile_open(entry_id)
        json_response(self, payload, status)

    def logout_browser_session(self):
        try:
            payload = json.loads(self.read_body().decode("utf-8"))
        except json.JSONDecodeError:
            payload = {}
        response, status = logout_browser_session(payload.get("siteKey") or payload.get("host") or "")
        json_response(self, response, status)

    def login_browser_session(self):
        try:
            payload = json.loads(self.read_body().decode("utf-8"))
        except json.JSONDecodeError:
            payload = {}
        response, status = login_browser_session(payload.get("siteKey") or payload.get("host") or "", payload.get("url") or "")
        json_response(self, response, status)

    def save_file(self):
        entry_id = safe_filename(self.headers.get("X-Clipy-Id"))
        file_id = safe_filename(self.headers.get("X-Clipy-File-Id") or entry_id)
        file_name = safe_filename(self.headers.get("X-File-Name"))
        file_type = unquote(self.headers.get("X-File-Type", ""))
        created_at = safe_int(self.headers.get("X-Created-At"), int(time.time() * 1000))
        batch_order = safe_int(self.headers.get("X-Batch-Order"), 0)
        batch_count = safe_int(self.headers.get("X-Clipy-Batch-Count"), 1)
        batch_title = sanitize_title(self.headers.get("X-Clipy-Batch-Title"))

        if batch_count > 1:
            self.save_file_to_batch(entry_id, file_id, file_name, file_type, created_at, batch_order, batch_count, batch_title)
            return

        file_dir = FILES_DIR / entry_id
        file_dir.mkdir(parents=True, exist_ok=True)
        file_path = file_dir / file_name
        file_path.write_bytes(self.read_body())

        entry = {
            "id": entry_id,
            "kind": "file",
            "sourceKey": "file",
            "sourceName": "文件",
            "title": file_name,
            "fileName": file_name,
            "fileType": file_type or "未知类型",
            "fileSize": file_path.stat().st_size,
            "filePath": str(file_path.relative_to(DATA_DIR)),
            "createdAt": created_at,
            "batchOrder": batch_order,
            "processingStatus": "processing",
        }
        upsert_entry(entry)
        start_background_entry_processing(entry)
        json_response(self, {"entry": entry})

    def save_file_to_batch(self, entry_id, file_id, file_name, file_type, created_at, batch_order, batch_count, batch_title):
        file_dir = FILES_DIR / entry_id / file_id
        file_dir.mkdir(parents=True, exist_ok=True)
        file_path = file_dir / file_name
        file_path.write_bytes(self.read_body())

        entries = read_entries()
        existing = next((item for item in entries if item.get("id") == entry_id and item.get("kind") == "file-batch"), None)
        files = list((existing or {}).get("files") or [])
        file_record = {
            "id": file_id,
            "fileName": file_name,
            "fileType": file_type or "未知类型",
            "fileSize": file_path.stat().st_size,
            "filePath": str(file_path.relative_to(DATA_DIR)),
            "batchOrder": batch_order,
        }
        files = [item for item in files if item.get("id") != file_id]
        files.append(file_record)
        files = sorted(files, key=lambda item: item.get("batchOrder", 0))

        entry = {
            "id": entry_id,
            "kind": "file-batch",
            "sourceKey": "file",
            "sourceName": "文件",
            "title": (existing or {}).get("title") or batch_title or build_file_batch_title(files, batch_count),
            "files": files,
            "fileCount": len(files),
            "expectedFileCount": batch_count,
            "fileSize": sum(item.get("fileSize", 0) for item in files),
            "createdAt": created_at,
            "processingStatus": "processing" if len(files) >= batch_count else "uploading",
        }
        upsert_entry(entry)
        if len(files) >= batch_count:
            start_background_entry_processing(entry)
        json_response(self, {"entry": entry})

    def open_entry(self, raw_id):
        entry_id, file_id = split_entry_file_id(raw_id)
        entry = next((item for item in read_entries() if item.get("id") == entry_id), None)
        if not entry:
            json_response(self, {"error": "Entry not found"}, 404)
            return

        if entry.get("kind") == "link":
            self.open_profile_entry(entry_id)
            return
        elif entry.get("kind") in {"file", "file-batch"}:
            file_record = get_file_record(entry, file_id)
            if not file_record:
                json_response(self, {"error": "File not found"}, 404)
                return
            file_path = (DATA_DIR / file_record.get("filePath", "")).resolve()
            if not is_data_file(file_path):
                json_response(self, {"error": "File not found"}, 404)
                return
            open_with_system(file_path)
        else:
            json_response(self, {"error": "Unsupported entry"}, 400)
            return

        json_response(self, {"ok": True})

    def serve_file(self, raw_id):
        entry_id, file_id = split_entry_file_id(raw_id)
        entry = next((item for item in read_entries() if item.get("id") == entry_id), None)
        if not entry or entry.get("kind") not in {"file", "file-batch"}:
            text_response(self, "File not found", 404)
            return

        file_record = get_file_record(entry, file_id)
        if not file_record:
            text_response(self, "File not found", 404)
            return

        file_path = (DATA_DIR / file_record.get("filePath", "")).resolve()
        if not is_data_file(file_path):
            text_response(self, "File not found", 404)
            return

        mime_type = file_record.get("fileType") or mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", mime_type)
        self.send_header("Content-Disposition", content_disposition(file_path.name))
        self.send_header("Content-Length", str(file_path.stat().st_size))
        self.end_headers()
        with file_path.open("rb") as file:
            shutil.copyfileobj(file, self.wfile)

    def serve_reader(self, raw_id):
        entry_id = unquote(raw_id)
        entry = next((item for item in read_entries(enrich_links=False) if item.get("id") == entry_id), None)
        if not entry:
            text_response(self, "Reader not found", 404)
            return

        item = entry.get("item") or {}
        content_path = (DATA_DIR / (item.get("contentPath") or "")).resolve()
        data_root = DATA_DIR.resolve()

        if content_path.exists() and content_path.is_file() and data_root in content_path.parents:
            html = reader_html_from_markdown(entry, content_path.read_text(encoding="utf-8", errors="replace"))
            html = rewrite_reader_asset_links(entry_id, html)
            html_response(self, html)
            return

        text_response(self, "Reader content not ready", 404)

    def serve_reader_asset(self, raw_path):
        parts = raw_path.split("/", 1)
        if len(parts) != 2:
            text_response(self, "Asset not found", 404)
            return
        entry_id = safe_filename(unquote(parts[0]))
        asset_name = safe_filename(unquote(parts[1]))
        asset_path = (ITEMS_DIR / entry_id / "assets" / asset_name).resolve()
        if not asset_path.exists() or not asset_path.is_file() or ITEMS_DIR.resolve() not in asset_path.parents:
            text_response(self, "Asset not found", 404)
            return
        content_type = mimetypes.guess_type(asset_path.name)[0] or "application/octet-stream"
        binary_response(self, asset_path.read_bytes(), content_type)

    def serve_logo(self, raw_url):
        if not raw_url:
            text_response(self, "Logo not found", 404)
            return

        logo_path = get_logo_file_for_url(unquote(raw_url))
        if not logo_path or not logo_path.exists():
            text_response(self, "Logo not found", 404)
            return

        content_type = mimetypes.guess_type(logo_path.name)[0] or "image/x-icon"
        binary_response(self, logo_path.read_bytes(), content_type)


def main():
    load_local_env()
    ensure_storage()
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 4173
    server = ThreadingHTTPServer(("127.0.0.1", port), ClipyHandler)
    print(f"Clipy is running at http://127.0.0.1:{port}/")
    print(f"Local data is saved in {DATA_DIR}")
    server.serve_forever()


if __name__ == "__main__":
    main()
