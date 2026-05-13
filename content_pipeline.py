from html import unescape
from pathlib import Path
from urllib.parse import quote, unquote, urljoin, urlparse
from urllib.request import Request, urlopen
import base64
import gzip
import json
import mimetypes
import os
import re
import time

MAX_FETCH_BYTES = 2_000_000
MAX_IMAGE_BYTES = 10_000_000
VIDEO_SOURCES = {"youtube", "bilibili"}
BROWSER_SNAPSHOTS_DIR = "browser_snapshots"


def capture_markdown_for_entry(entry, data_dir, force=False):
    if entry.get("kind") != "link" or not entry.get("url"):
        return entry

    entry = dict(entry)
    markdown_path = existing_markdown_path(entry, data_dir)
    if not force and entry.get("content", {}).get("status") == "ready" and markdown_path and markdown_path.exists():
        return entry

    source_key = entry.get("sourceKey") or "web"
    if source_key in VIDEO_SOURCES:
        entry["content"] = {
            "status": "skipped_video",
            "format": "markdown",
            "reason": "视频内容之后接入专门转录流程",
            "capturedAt": now_ms(),
        }
        return entry

    try:
        result = normalize_result(crawl_entry(entry), entry)
        apply_result_title(entry, result)
        access_requirement = detect_content_access_requirement(entry, result)
        if access_requirement:
            markdown_info = write_markdown(entry, result, data_dir) if result.get("text") or result.get("images") else {}
            html_path = write_html_snapshot(entry, result.get("rawHtml") or "", data_dir)
            entry["content"] = {
                "status": "needs_login",
                "format": "markdown",
                "reason": access_requirement,
                "partial": True,
                "markdownPath": markdown_info.get("markdownPath", ""),
                "htmlPath": html_path,
                "assetsPath": markdown_info.get("assetsPath", ""),
                "imageCount": markdown_info.get("imageCount", 0),
                "finalUrl": result.get("finalUrl") or "",
                "capturedAt": now_ms(),
            }
            return entry

        if not result.get("text") and not result.get("images"):
            entry["content"] = {
                "status": "empty",
                "format": "markdown",
                "reason": "没有抓到可保存的正文或图片",
                "capturedAt": now_ms(),
            }
            return entry

        markdown_info = write_markdown(entry, result, data_dir)
        html_path = write_html_snapshot(entry, result.get("rawHtml") or "", data_dir)
        entry["content"] = {
            "status": "ready",
            "format": "markdown",
            "markdownPath": markdown_info["markdownPath"],
            "htmlPath": html_path,
            "assetsPath": markdown_info.get("assetsPath", ""),
            "imageCount": markdown_info["imageCount"],
            "finalUrl": result.get("finalUrl") or "",
            "siteName": result.get("siteName") or "",
            "description": result.get("description") or "",
            "capturedAt": now_ms(),
        }
    except Exception as error:
        entry["content"] = {
            "status": "failed",
            "format": "markdown",
            "reason": str(error)[:160],
            "capturedAt": now_ms(),
        }
    return entry


def capture_browser_result_for_entry(entry, data_dir, browser_result):
    if entry.get("kind") != "link" or not entry.get("url"):
        return entry

    entry = dict(entry)
    browser_result = dict(browser_result or {})
    status = browser_result.get("status") or "failed"
    captured_at = now_ms()

    if (entry.get("sourceKey") or "web") in VIDEO_SOURCES:
        entry["content"] = {
            "status": "skipped_video",
            "format": "markdown",
            "method": "browser_profile",
            "reason": "视频内容之后接入专门转录流程；浏览器 profile 先用于标题和元信息读取",
            "capturedAt": captured_at,
        }
        return entry

    if status == "login_required":
        entry["content"] = {
            "status": "needs_login",
            "format": "markdown",
            "method": "browser_profile",
            "reason": "需要在 Clipy 的浏览器 profile 里登录该来源后再读取",
            "capturedAt": captured_at,
        }
        return entry

    if status == "blocked":
        html_path = write_browser_html_snapshot(entry, browser_result, data_dir)
        entry["content"] = {
            "status": "blocked",
            "format": "markdown",
            "method": "browser_profile",
            "reason": "站点返回了安全验证、机器人检测或访问限制页面",
            "htmlPath": html_path,
            "capturedAt": captured_at,
        }
        return entry

    if status == "failed":
        entry["content"] = {
            "status": "failed",
            "format": "markdown",
            "method": "browser_profile",
            "reason": browser_result.get("reason") or "浏览器 profile 读取失败",
            "capturedAt": captured_at,
        }
        return entry

    text = clean_text(browser_result.get("text") or "")
    images = browser_result.get("images") or []
    if not text and not images:
        html_path = write_browser_html_snapshot(entry, browser_result, data_dir)
        entry["content"] = {
            "status": "empty",
            "format": "markdown",
            "method": "browser_profile",
            "reason": "浏览器打开了页面，但没有读到可保存的正文或图片",
            "htmlPath": html_path,
            "capturedAt": captured_at,
        }
        return entry

    result = {
        "title": clean_inline(browser_result.get("title") or browser_result.get("documentTitle") or entry.get("title") or ""),
        "text": text,
        "author": clean_inline(browser_result.get("author") or ""),
        "publishedAt": clean_inline(browser_result.get("publishedAt") or ""),
        "images": dedupe_images(images),
    }
    access_requirement = detect_content_access_requirement(entry, result)
    if access_requirement:
        markdown_info = write_markdown(entry, result, data_dir)
        html_path = write_browser_html_snapshot(entry, browser_result, data_dir)
        entry["content"] = {
            "status": "needs_login",
            "format": "markdown",
            "method": "browser_profile",
            "reason": access_requirement,
            "partial": True,
            "markdownPath": markdown_info["markdownPath"],
            "assetsPath": markdown_info.get("assetsPath", ""),
            "htmlPath": html_path,
            "finalUrl": browser_result.get("finalUrl") or "",
            "imageCount": markdown_info["imageCount"],
            "textLength": len(text),
            "capturedAt": captured_at,
        }
        return entry

    markdown_info = write_markdown(entry, result, data_dir)
    html_path = write_browser_html_snapshot(entry, browser_result, data_dir)
    entry["content"] = {
        "status": "ready",
        "format": "markdown",
        "method": "browser_profile",
        "markdownPath": markdown_info["markdownPath"],
        "assetsPath": markdown_info.get("assetsPath", ""),
        "htmlPath": html_path,
        "finalUrl": browser_result.get("finalUrl") or "",
        "imageCount": markdown_info["imageCount"],
        "textLength": len(text),
        "capturedAt": captured_at,
    }
    return entry


def existing_markdown_path(entry, data_dir):
    path = entry.get("content", {}).get("markdownPath")
    if not path:
        return None
    return (Path(data_dir) / path).resolve()


def write_browser_html_snapshot(entry, browser_result, data_dir):
    return write_html_snapshot(entry, browser_result.get("html") or "", data_dir)


def write_html_snapshot(entry, html, data_dir):
    if not html:
        return ""

    data_dir = Path(data_dir)
    snapshots_dir = data_dir / BROWSER_SNAPSHOTS_DIR
    snapshots_dir.mkdir(parents=True, exist_ok=True)
    path = snapshots_dir / f"{entry['id']}.html"
    path.write_text(html, encoding="utf-8")
    return str(path.relative_to(data_dir))


def crawl_entry(entry):
    source_key = entry.get("sourceKey") or "web"
    if source_key == "x":
        return crawl_x(entry)
    if source_key == "weibo":
        return crawl_weibo(entry)
    if source_key in {"wechat", "zhihu", "web", "wsj", "ft", "reddit"}:
        return crawl_article_page(entry)
    if source_key in {"xhs", "dianping"}:
        return crawl_share_or_page(entry)
    if source_key == "github":
        return crawl_github(entry)
    return crawl_article_page(entry)


def normalize_result(result, entry):
    result = dict(result or {})
    if not result:
        return result

    source_key = entry.get("sourceKey")
    text = result.get("text") or ""
    if source_key in {"xhs", "dianping"}:
        text = clean_share_page_noise(text)
        result["images"] = clean_share_page_images(result.get("images") or [])
    result["text"] = text
    return result


def detect_content_access_requirement(entry, result):
    source_key = entry.get("sourceKey") or ""
    text = clean_text(result.get("text") or "")
    lower_text = text.lower()
    title = clean_inline(result.get("title") or entry.get("title") or "")

    if source_key == "wsj":
        has_dow_jones_footer = "dow jones" in lower_text and "all rights reserved" in lower_text
        looks_like_preview = len(text) < 2600 or re.search(r"\blisten\s*\n+\s*\(\d+\s*min\)", lower_text)
        if has_dow_jones_footer and looks_like_preview:
            return "WSJ 只返回了付费墙前的预览内容，需要在 Clipy 浏览器里登录后补全"

    if source_key == "ft":
        ft_paywall_signals = [
            "subscribe to unlock this article",
            "try unlimited access",
            "only $1 for 4 weeks",
            "keep reading for $1",
            "complete digital access to quality ft journalism",
        ]
        if any(signal in lower_text for signal in ft_paywall_signals):
            return "FT 只返回了订阅墙页面，需要在 Clipy 浏览器里登录或订阅后补全"

    paywall_signals = [
        "subscribe to continue",
        "sign in to continue",
        "sign in to read",
        "log in to continue",
        "already a subscriber",
        "to read the full story",
        "continue reading with a subscription",
        "subscribe to unlock this article",
        "try unlimited access",
        "keep reading for $1",
        "订阅后继续",
        "登录后继续",
        "阅读全文",
    ]
    if any(signal in lower_text for signal in paywall_signals) and len(text) < 12000:
        host = urlparse(entry.get("url") or "").hostname or "该来源"
        return f"{host} 需要登录或订阅后才能保存完整正文"

    blocked_titles = {"are you a robot?", "access denied", "forbidden"}
    if title.lower() in blocked_titles:
        return "站点返回了验证页面，需要在 Clipy 浏览器里验证后补全"

    return ""


def apply_result_title(entry, result):
    if entry.get("renamedAt"):
        return
    title = clean_inline(result.get("title") or "")
    if not title or not should_replace_entry_title(entry):
        return
    entry["title"] = shorten_inline(title, 120)
    entry["metadataTitle"] = entry["title"]
    entry["metadataFetchedAt"] = now_ms()
    entry["metadataSource"] = result.get("method") or "content_pipeline"


def should_replace_entry_title(entry):
    title = clean_inline(entry.get("title") or "")
    source_key = entry.get("sourceKey") or ""
    generic = {
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
    if title in generic:
        return True
    if source_key == "x" and re.fullmatch(r"@[\w.-]+\s+的帖子", title):
        return True
    if source_key == "weibo" and re.fullmatch(r"微博\s+\d+", title):
        return True
    if source_key == "wsj" and re.search(r"\b[0-9a-f]{8,}$", title.lower()):
        return True
    if title.startswith("mp.weixin.qq.com /") or title.startswith("bilibili.com /"):
        return True
    if " / " in title and not re.search(r"[\u4e00-\u9fff]", title):
        return True
    return False


def shorten_inline(value, max_length):
    value = clean_inline(value)
    if len(value) <= max_length:
        return value
    return f"{value[: max_length - 1]}…"


def clean_share_page_noise(text):
    noisy_exact = {
        "",
        "-",
        "关注",
        "评论",
        "发现",
        "直播",
        "发布",
        "App内打开",
        "打开App查看完整内容",
        "打开App去APP查看更多内容",
        "说点什么吧...",
    }
    cleaned = []
    for raw_line in (text or "").splitlines():
        line = re.sub(r"^[-•]\s*", "", raw_line.strip())
        if line in noisy_exact:
            continue
        if re.fullmatch(r"\d+/\d+|\d+", line):
            continue
        if "打开App" in line or "打开 App" in line:
            continue
        if "把内容复制好" in line:
            continue
        cleaned.append(line)
    return clean_text("\n".join(cleaned))


def clean_share_page_images(images):
    noisy_alt = {"点评LOGO", "大众点评", "发现好去处"}
    cleaned = []
    for image in images:
        alt = image.get("alt") or ""
        url = image.get("url") or ""
        if alt in noisy_alt:
            continue
        if "logo" in alt.lower() or "logo" in url.lower():
            continue
        cleaned.append(image)
    return cleaned


def crawl_x(entry):
    url = entry["url"]
    parsed = urlparse(url)
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 3 or parts[1] != "status":
        return {}

    username = parts[0]
    status_id = parts[2]

    result = crawl_x_fxtwitter(username, status_id)
    if result.get("text") or result.get("images"):
        return result

    result = crawl_x_syndication(username, status_id)
    if result.get("text") or result.get("images"):
        return result

    result = crawl_x_oembed(url, username)
    if result.get("text"):
        return result

    return from_share_text(entry)


def crawl_x_fxtwitter(username, status_id):
    try:
        data = fetch_json(f"https://api.fxtwitter.com/{quote(username)}/status/{quote(status_id)}", timeout=7)
    except Exception:
        return {}

    tweet = data.get("tweet") or {}
    author = tweet.get("author") or {}
    article = tweet.get("article") if isinstance(tweet.get("article"), dict) else {}
    images = extract_x_images(tweet)
    article_text = extract_x_article_text(article)
    tweet_text = extract_x_text(tweet.get("text")) or extract_x_text(tweet.get("raw_text"))
    if re.fullmatch(r"https?://\S+", tweet_text):
        tweet_text = ""
    text = clean_text("\n\n".join(part for part in [tweet_text, article_text] if part))
    title = clean_inline(article.get("title") or tweet_text or text)

    return {
        "title": title,
        "text": text,
        "author": author.get("name") or author.get("screen_name") or username,
        "publishedAt": tweet.get("created_at") or "",
        "images": images,
    }


def crawl_x_syndication(username, status_id):
    endpoints = [
        f"https://cdn.syndication.twimg.com/tweet-result?id={quote(status_id)}&lang=zh-cn&token=a",
        f"https://cdn.syndication.twimg.com/tweet-result?id={quote(status_id)}&lang=en&token=a",
        f"https://cdn.syndication.twimg.com/tweet-result?id={quote(status_id)}&lang=zh-cn",
        f"https://cdn.syndication.twimg.com/tweet-result?id={quote(status_id)}&lang=en",
    ]
    for endpoint in endpoints:
        try:
            data = fetch_json(endpoint, timeout=6)
        except Exception:
            continue
        if not isinstance(data, dict) or not data:
            continue

        images = []
        for item in data.get("photos") or []:
            if item.get("url"):
                images.append({"url": item["url"], "alt": "X 图片"})
        for item in data.get("mediaDetails") or []:
            image_url = item.get("media_url_https")
            if image_url:
                images.append({"url": image_url, "alt": item.get("display_url") or "X 图片"})

        user = data.get("user") or {}
        return {
            "title": data.get("text") or "",
            "text": clean_text(data.get("text") or ""),
            "author": user.get("name") or user.get("screen_name") or username,
            "publishedAt": data.get("created_at") or "",
            "images": dedupe_images(images),
        }
    return {}


def extract_x_text(value):
    if isinstance(value, str):
        return clean_text(value)
    if isinstance(value, dict):
        return clean_text(value.get("text") or "")
    return ""


def extract_x_article_text(article):
    if not isinstance(article, dict):
        return ""

    parts = []
    append_unique_text(parts, article.get("title") or "")
    content = article.get("content") or {}
    has_blocks = False
    for block in content.get("blocks") or []:
        if not isinstance(block, dict):
            continue
        text = clean_text(block.get("text") or "")
        if not text:
            continue
        has_blocks = True
        if block.get("type") in {"header-one", "header-two", "header-three"}:
            append_unique_text(parts, f"## {text}")
        else:
            append_unique_text(parts, text)
    if not has_blocks:
        append_unique_text(parts, article.get("preview_text") or "")
    return clean_text("\n\n".join(part for part in parts if part))


def append_unique_text(parts, text):
    text = clean_text(text)
    if not text:
        return
    comparable = text.lstrip("# ").strip()
    existing = {part.lstrip("# ").strip() for part in parts}
    if comparable not in existing:
        parts.append(text)


def extract_x_images(tweet):
    images = []
    media = tweet.get("media") if isinstance(tweet.get("media"), dict) else {}
    for item in media.get("photos") or media.get("all") or []:
        image_url = item.get("url")
        if image_url:
            images.append({"url": image_url, "alt": "X 图片"})

    article = tweet.get("article") if isinstance(tweet.get("article"), dict) else {}
    cover = article.get("cover_media") if isinstance(article.get("cover_media"), dict) else {}
    media_info = cover.get("media_info") if isinstance(cover.get("media_info"), dict) else {}
    cover_url = media_info.get("original_img_url") or media_info.get("url")
    if cover_url:
        images.append({"url": cover_url, "alt": article.get("title") or "X Article 封面"})

    return dedupe_images(images)


def crawl_x_oembed(url, username):
    try:
        data = fetch_json(f"https://publish.twitter.com/oembed?url={quote(url, safe='')}", timeout=6)
    except Exception:
        return {}

    html = data.get("html") or ""
    paragraph_match = re.search(r"<p\b[^>]*>(.*?)</p>", html, re.I | re.S)
    text_html = paragraph_match.group(1) if paragraph_match else html
    text = html_to_text(text_html)
    return {
        "title": text,
        "text": text,
        "author": data.get("author_name") or username,
        "images": [],
    }


def crawl_weibo(entry):
    url = entry["url"]
    status_id = get_weibo_status_id(url)
    if not status_id:
        return from_share_text(entry)

    endpoints = [
        f"https://m.weibo.cn/statuses/show?id={quote(status_id)}",
        f"https://weibo.com/ajax/statuses/show?id={quote(status_id)}",
    ]
    for endpoint in endpoints:
        try:
            data = fetch_json(
                endpoint,
                timeout=7,
                headers={
                    "Referer": "https://weibo.com/",
                    "X-Requested-With": "XMLHttpRequest",
                },
            )
        except Exception:
            continue
        status = data.get("data") if isinstance(data.get("data"), dict) else data
        if not isinstance(status, dict) or data.get("ok") == -100:
            continue

        text = status.get("text_raw") or status.get("text") or status.get("longTextContent") or ""
        images = extract_weibo_images(status)
        user = status.get("user") or {}
        result = {
            "title": text,
            "text": html_to_text(text),
            "author": user.get("screen_name") or user.get("name") or "",
            "publishedAt": status.get("created_at") or "",
            "images": images,
        }
        if result["text"] or result["images"]:
            return result
    return from_share_text(entry)


def get_weibo_status_id(url):
    parsed = urlparse(url)
    parts = [part for part in parsed.path.split("/") if part]
    query_id = {}
    try:
        from urllib.parse import parse_qs

        query_id = parse_qs(parsed.query)
    except Exception:
        query_id = {}
    if query_id.get("id"):
        return query_id["id"][0]
    return parts[-1] if parts else ""


def extract_weibo_images(status):
    images = []
    pic_infos = status.get("pic_infos") or {}
    for pic_id in status.get("pic_ids") or []:
        info = pic_infos.get(pic_id) or {}
        for key in ["original", "largest", "large", "bmiddle", "thumbnail"]:
            candidate = info.get(key) or {}
            image_url = candidate.get("url")
            if image_url:
                images.append({"url": image_url, "alt": "微博图片"})
                break

    for key in ["original_pic", "bmiddle_pic", "thumbnail_pic"]:
        if status.get(key):
            images.append({"url": status[key], "alt": "微博图片"})

    return dedupe_images(images)


def crawl_share_or_page(entry):
    share = from_share_text(entry)
    page = crawl_article_page(entry)
    if page.get("text") or page.get("images"):
        if share.get("text") and share["text"] not in page.get("text", ""):
            page["text"] = f"{share['text']}\n\n{page.get('text', '')}".strip()
        return page
    return share


def crawl_github(entry):
    repo = parse_github_repo(entry.get("url") or "")
    if not repo:
        return crawl_article_page(entry)

    owner, repo_name = repo
    repo_data = fetch_github_json(f"https://api.github.com/repos/{quote(owner)}/{quote(repo_name)}")
    if not isinstance(repo_data, dict) or repo_data.get("message") == "Not Found":
        return crawl_article_page(entry)

    path_parts = [part for part in urlparse(entry.get("url") or "").path.split("/") if part]
    issue_or_pr = crawl_github_issue_or_pr(entry, repo_data, owner, repo_name, path_parts)
    if issue_or_pr:
        return issue_or_pr

    readme = fetch_github_readme(owner, repo_name)
    text = build_github_repo_text(repo_data, readme)
    title = github_repo_title(repo_data, owner, repo_name)
    owner_info = repo_data.get("owner") or {}
    return {
        "title": title,
        "text": text,
        "author": owner_info.get("login") or owner,
        "publishedAt": repo_data.get("created_at") or "",
        "images": [],
    }


def crawl_github_issue_or_pr(entry, repo_data, owner, repo_name, path_parts):
    if len(path_parts) < 4 or path_parts[2] not in {"issues", "pull"} or not path_parts[3].isdigit():
        return None

    number = path_parts[3]
    data = fetch_github_json(f"https://api.github.com/repos/{quote(owner)}/{quote(repo_name)}/issues/{quote(number)}")
    if not isinstance(data, dict) or data.get("message") == "Not Found":
        return None

    user = data.get("user") or {}
    is_pull = path_parts[2] == "pull" or bool(data.get("pull_request"))
    kind = "Pull Request" if is_pull else "Issue"
    title = clean_inline(data.get("title") or f"{repo_data.get('full_name') or owner + '/' + repo_name} {kind} #{number}")
    body = clean_text(data.get("body") or "")
    lines = [
        f"# {title}",
        "",
        f"- 仓库：{repo_data.get('full_name') or owner + '/' + repo_name}",
        f"- 类型：{kind}",
        f"- 编号：#{number}",
        f"- 状态：{data.get('state') or ''}",
        f"- 作者：{user.get('login') or ''}",
        f"- 创建时间：{data.get('created_at') or ''}",
        f"- 更新时间：{data.get('updated_at') or ''}",
        f"- 评论数：{data.get('comments') or 0}",
    ]
    labels = [label.get("name") for label in data.get("labels") or [] if isinstance(label, dict) and label.get("name")]
    if labels:
        lines.append(f"- 标签：{', '.join(labels)}")
    if body:
        lines.extend(["", "## 正文", "", body])

    return {
        "title": f"{repo_data.get('full_name') or owner + '/' + repo_name} #{number}：{title}",
        "text": clean_text("\n".join(lines)),
        "author": user.get("login") or "",
        "publishedAt": data.get("created_at") or "",
        "images": [],
    }


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


def fetch_github_json(url):
    return fetch_json(
        url,
        timeout=8,
        headers={
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )


def fetch_github_readme(owner, repo_name):
    try:
        data = fetch_github_json(f"https://api.github.com/repos/{quote(owner)}/{quote(repo_name)}/readme")
    except Exception:
        return ""
    if not isinstance(data, dict):
        return ""
    content = data.get("content") or ""
    if data.get("encoding") != "base64" or not content:
        return ""
    try:
        return base64.b64decode(content.replace("\n", "")).decode("utf-8", errors="replace")
    except Exception:
        return ""


def github_repo_title(repo_data, owner, repo_name):
    full_name = repo_data.get("full_name") or f"{owner}/{repo_name}"
    description = clean_inline(repo_data.get("description") or "")
    return f"{full_name}：{description}" if description else full_name


def build_github_repo_text(repo_data, readme):
    full_name = repo_data.get("full_name") or repo_data.get("name") or "GitHub Repository"
    owner = repo_data.get("owner") or {}
    license_info = repo_data.get("license") or {}
    topics = repo_data.get("topics") or []
    lines = [
        f"# {github_repo_title(repo_data, owner.get('login') or '', repo_data.get('name') or '')}",
        "",
        "## 仓库信息",
        "",
        f"- 仓库：{full_name}",
        f"- 所有者：{owner.get('login') or ''}",
        f"- 描述：{repo_data.get('description') or ''}",
        f"- 默认分支：{repo_data.get('default_branch') or ''}",
        f"- 主要语言：{repo_data.get('language') or ''}",
        f"- License：{license_info.get('spdx_id') or license_info.get('name') or ''}",
        f"- Stars：{repo_data.get('stargazers_count', 0)}",
        f"- Forks：{repo_data.get('forks_count', 0)}",
        f"- Open issues：{repo_data.get('open_issues_count', 0)}",
        f"- 创建时间：{repo_data.get('created_at') or ''}",
        f"- 更新时间：{repo_data.get('updated_at') or ''}",
        f"- 推送时间：{repo_data.get('pushed_at') or ''}",
        f"- 项目主页：{repo_data.get('homepage') or ''}",
    ]
    if topics:
        lines.append(f"- Topics：{', '.join(topics)}")
    lines.append(f"- GitHub：{repo_data.get('html_url') or ''}")

    if readme:
        lines.extend(["", "## README", "", readme.strip()])
    else:
        lines.extend(["", "## README", "", "没有通过公开 GitHub API 读到 README。若这是私有仓库，需要后续接入 Clipy 浏览器 profile 页面读取或用户授权。"])

    return clean_text("\n".join(lines))


def from_share_text(entry):
    text = remove_urls(entry.get("rawText") or "")
    if not text:
        text = entry.get("title") or ""
    return {
        "title": entry.get("title") or text,
        "text": clean_text(text),
        "author": "",
        "publishedAt": "",
        "images": [],
    }


def crawl_article_page(entry):
    try:
        html, final_url = fetch_html(entry["url"])
    except Exception:
        share_text = remove_urls(entry.get("rawText") or "")
        if share_text:
            return from_share_text(entry)
        return {
            "title": entry.get("title") or "",
            "text": "",
            "author": "",
            "publishedAt": "",
            "images": [],
        }

    clean_html = strip_noise_html(html)
    block = select_article_block(clean_html, entry.get("sourceKey"))
    if not block:
        block = clean_html

    text = html_to_text(block)
    images = extract_images(block, final_url)
    if not images:
        og_image = find_meta_content(html, "property", "og:image") or find_meta_content(html, "name", "twitter:image")
        if og_image:
            images.append({"url": urljoin(final_url, og_image), "alt": "页面图片"})

    title = (
        find_meta_content(html, "property", "og:title")
        or find_meta_content(html, "name", "twitter:title")
        or find_tag_title(html)
        or entry.get("title")
        or ""
    )
    site_name = find_meta_content(html, "property", "og:site_name") or (urlparse(final_url).hostname or "")
    description = find_meta_content(html, "property", "og:description") or find_meta_content(html, "name", "description")
    author = (
        find_meta_content(html, "name", "author")
        or find_meta_content(html, "property", "article:author")
        or find_meta_content(html, "name", "weixin:author")
        or find_meta_content(html, "itemprop", "author")
    )
    published_at = (
        find_meta_content(html, "property", "article:published_time")
        or find_meta_content(html, "name", "publishdate")
        or find_meta_content(html, "name", "pubdate")
        or find_meta_content(html, "itemprop", "datePublished")
    )

    if not text:
        text = remove_urls(entry.get("rawText") or "")
    return {
        "title": clean_inline(title),
        "text": text,
        "author": clean_inline(author),
        "publishedAt": clean_inline(published_at),
        "siteName": clean_inline(site_name),
        "description": clean_inline(description),
        "finalUrl": final_url,
        "rawHtml": html,
        "images": dedupe_images(images),
    }


def select_article_block(html, source_key):
    if source_key == "wechat":
        block = find_block_by_id(html, "js_content")
        if block:
            return block

    candidates = []
    for tag_name in ["article", "main", "section", "div"]:
        for block in find_tag_blocks(html, tag_name):
            attrs = parse_attrs(block[: min(len(block), 1000)])
            marker = " ".join([attrs.get("id", ""), attrs.get("class", ""), attrs.get("role", "")]).lower()
            if tag_name in {"article", "main"} or re.search(r"article|content|post|entry|story|rich_media_content|正文|文章", marker):
                candidates.append(block)

    if not candidates:
        return ""
    return max(candidates, key=article_score)


def find_tag_blocks(html, tag_name):
    blocks = []
    tag_pattern = re.compile(rf"</?{tag_name}\b[^>]*>", re.I)
    for match in tag_pattern.finditer(html or ""):
        tag = match.group(0)
        if tag.startswith("</"):
            continue
        start = match.start()
        depth = 0
        for tag_match in tag_pattern.finditer(html, match.start()):
            current = tag_match.group(0)
            if current.startswith("</"):
                depth -= 1
                if depth == 0:
                    blocks.append(html[start : tag_match.end()])
                    break
            else:
                depth += 1
    return blocks[:80]


def article_score(html):
    text = html_to_text(html)
    if len(text) < 80:
        return 0
    paragraph_count = len(re.findall(r"</p>|<br\s*/?>", html or "", re.I))
    link_text = html_to_text(" ".join(match.group(1) for match in re.finditer(r"<a\b[^>]*>(.*?)</a>", html or "", re.I | re.S)))
    link_penalty = min(len(link_text) / max(len(text), 1), 0.8)
    punctuation_bonus = len(re.findall(r"[。！？.!?]", text))
    image_bonus = min(len(re.findall(r"<img\b", html or "", re.I)), 8) * 80
    return len(text) * (1 - link_penalty) + paragraph_count * 120 + punctuation_bonus * 20 + image_bonus


def find_block_by_id(html, element_id):
    match = re.search(rf"<([a-z0-9]+)\b[^>]*id=[\"']{re.escape(element_id)}[\"'][^>]*>", html, re.I)
    if not match:
        return ""

    start = match.start()
    tag_name = match.group(1).lower()
    depth = 0
    tag_pattern = re.compile(rf"</?{tag_name}\b[^>]*>", re.I)
    for tag_match in tag_pattern.finditer(html, match.start()):
        tag = tag_match.group(0)
        if tag.startswith("</"):
            depth -= 1
            if depth == 0:
                return html[start : tag_match.end()]
        else:
            depth += 1
    return html[start:]


def write_markdown(entry, result, data_dir):
    data_dir = Path(data_dir)
    markdown_dir = data_dir / "markdown"
    assets_dir = data_dir / "markdown_assets" / entry["id"]
    markdown_dir.mkdir(parents=True, exist_ok=True)
    if assets_dir.exists():
        for existing in assets_dir.iterdir():
            if existing.is_file():
                existing.unlink()

    downloaded_images = download_images(result.get("images") or [], assets_dir)
    title = clean_inline(entry.get("title") or result.get("title") or "未命名收藏")
    markdown_name = f"{safe_slug(title)[:60] or 'clipy'}-{entry['id']}.md"
    markdown_path = markdown_dir / markdown_name
    relative_markdown_path = markdown_path.relative_to(data_dir)

    body = build_markdown(entry, result, downloaded_images, data_dir)
    markdown_path.write_text(body, encoding="utf-8")

    return {
        "markdownPath": str(relative_markdown_path),
        "assetsPath": str(assets_dir.relative_to(data_dir)) if downloaded_images else "",
        "imageCount": len(downloaded_images),
    }


def build_markdown(entry, result, images, data_dir):
    title = clean_inline(entry.get("title") or result.get("title") or "未命名收藏")
    text = result.get("text") or ""
    source_name = entry.get("sourceName") or entry.get("sourceKey") or "网页"
    captured_at = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())

    lines = [
        "---",
        f"id: {yaml_value(entry.get('id', ''))}",
        f"source: {yaml_value(source_name)}",
        f"source_key: {yaml_value(entry.get('sourceKey', ''))}",
        f"url: {yaml_value(entry.get('url', ''))}",
        f"title: {yaml_value(title)}",
        f"captured_at: {yaml_value(captured_at)}",
    ]
    if result.get("author"):
        lines.append(f"author: {yaml_value(result['author'])}")
    if result.get("publishedAt"):
        lines.append(f"published_at: {yaml_value(result['publishedAt'])}")
    lines.extend(["---", "", f"# {title}", ""])

    lines.extend(
        [
            f"- 来源：{source_name}",
            f"- 原链接：{entry.get('url', '')}",
            f"- 保存时间：{captured_at}",
        ]
    )
    if result.get("author"):
        lines.append(f"- 作者：{result['author']}")
    if result.get("publishedAt"):
        lines.append(f"- 发布时间：{result['publishedAt']}")

    if text:
        lines.extend(["", "## 正文", "", text.strip(), ""])

    if images:
        lines.extend(["", "## 图片", ""])
        for index, image in enumerate(images, start=1):
            relative_path = os.path.relpath(image["path"], data_dir / "markdown")
            alt = image.get("alt") or f"图片 {index}"
            lines.extend([f"![{alt}]({Path(relative_path).as_posix()})", ""])

    return "\n".join(lines).strip() + "\n"


def download_images(images, assets_dir):
    downloaded = []
    for image in dedupe_images(images)[:12]:
        url = image.get("url")
        if not url or url.startswith("data:"):
            continue
        try:
            body, content_type = fetch_binary(url, MAX_IMAGE_BYTES)
        except Exception:
            continue
        extension = media_extension(content_type, url)
        if not extension:
            continue
        assets_dir.mkdir(parents=True, exist_ok=True)
        path = assets_dir / f"image-{len(downloaded) + 1:02d}{extension}"
        path.write_bytes(body)
        downloaded.append({"path": path, "alt": image.get("alt") or f"图片 {len(downloaded) + 1}"})
    return downloaded


def fetch_html(url):
    request = Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Accept-Encoding": "identity",
        },
    )
    with urlopen(request, timeout=8) as response:
        content_type = response.headers.get("Content-Type", "")
        body = response.read(MAX_FETCH_BYTES)
        if response.headers.get("Content-Encoding") == "gzip":
            body = gzip.decompress(body)
        return decode_html(body, content_type), response.geturl()


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
        body = response.read(MAX_FETCH_BYTES)
        if response.headers.get("Content-Encoding") == "gzip":
            body = gzip.decompress(body)
        return json.loads(decode_html(body, response.headers.get("Content-Type", "")))


def fetch_binary(url, max_bytes):
    parsed = urlparse(url)
    referer = f"{parsed.scheme}://{parsed.netloc}/" if parsed.scheme and parsed.netloc else ""
    if parsed.hostname and "sinaimg.cn" in parsed.hostname:
        referer = "https://weibo.com/"
    request = Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            ),
            "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
            "Referer": referer,
        },
    )
    with urlopen(request, timeout=10) as response:
        body = response.read(max_bytes + 1)
        if len(body) > max_bytes:
            raise ValueError("image too large")
        return body, response.headers.get("Content-Type", "").split(";")[0].strip().lower()


def extract_images(html, base_url):
    images = []
    for match in re.finditer(r"<img\b[^>]*>", html, re.I):
        attrs = parse_attrs(match.group(0))
        src = attrs.get("data-src") or attrs.get("data-original") or attrs.get("data-url") or attrs.get("src")
        if not src:
            continue
        images.append({"url": urljoin(base_url, unescape(src)), "alt": clean_inline(attrs.get("alt") or "图片")})
    return dedupe_images(images)


def html_to_text(html):
    html = strip_noise_html(html)
    html = re.sub(r"<br\s*/?>", "\n", html, flags=re.I)
    html = re.sub(r"</(?:p|div|section|article|li|h[1-6]|blockquote|tr)>", "\n\n", html, flags=re.I)
    html = re.sub(r"<li\b[^>]*>", "- ", html, flags=re.I)
    html = re.sub(r"<[^>]+>", "", html)
    return clean_text(html)


def strip_noise_html(html):
    html = html or ""
    for tag_name in ["script", "style", "noscript", "svg", "canvas", "iframe", "form", "button"]:
        html = re.sub(rf"<{tag_name}\b.*?</{tag_name}>", "", html, flags=re.I | re.S)
    html = re.sub(r"<(?:header|footer|nav|aside)\b.*?</(?:header|footer|nav|aside)>", "", html, flags=re.I | re.S)
    noise_pattern = (
        r"(?:comment|comments|reply|related|recommend|promo|advert|ad-|ads|"
        r"banner|cookie|subscribe|newsletter|share|social|sidebar|menu|nav|footer|header)"
    )
    html = re.sub(
        rf"<([a-z0-9]+)\b[^>]*(?:class|id)=[\"'][^\"']*{noise_pattern}[^\"']*[\"'][^>]*>.*?</\1>",
        "",
        html,
        flags=re.I | re.S,
    )
    return html


def clean_text(value):
    value = unescape(value or "")
    value = value.replace("\r", "\n")
    value = re.sub(r"\n[ \t]+", "\n", value)
    value = re.sub(r"[ \t]{2,}", " ", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def clean_inline(value):
    return re.sub(r"\s+", " ", clean_text(value)).strip()


def remove_urls(value):
    value = re.sub(r"https?://\S+", "", value or "")
    return clean_text(value)


def parse_attrs(tag):
    attrs = {}
    for attr_match in re.finditer(r'([:\w-]+)\s*=\s*("([^"]*)"|\'([^\']*)\'|([^\s>]+))', tag, re.I):
        attr_value = attr_match.group(3) or attr_match.group(4) or attr_match.group(5) or ""
        attrs[attr_match.group(1).lower()] = unescape(attr_value)
    return attrs


def find_meta_content(html, key, value):
    for match in re.finditer(r"<meta\b[^>]*>", html or "", re.I):
        attrs = parse_attrs(match.group(0))
        if attrs.get(key.lower(), "").lower() == value.lower() and attrs.get("content"):
            return attrs["content"]
    return ""


def find_tag_title(html):
    match = re.search(r"<title[^>]*>(.*?)</title>", html or "", re.I | re.S)
    return html_to_text(match.group(1)) if match else ""


def dedupe_images(images):
    seen = set()
    result = []
    for image in images or []:
        url = image.get("url")
        if not url or url in seen:
            continue
        seen.add(url)
        result.append(image)
    return result


def media_extension(content_type, url):
    guessed = mimetypes.guess_extension(content_type or "")
    if guessed:
        return ".jpg" if guessed == ".jpe" else guessed

    path = urlparse(url).path.lower()
    for extension in [".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"]:
        if path.endswith(extension):
            return ".jpg" if extension == ".jpeg" else extension
    return ""


def decode_html(body, content_type):
    charset_match = re.search(r"charset=([\w.-]+)", content_type or "", re.I)
    encodings = [charset_match.group(1)] if charset_match else []
    encodings.extend(["utf-8", "gb18030", "big5", "latin-1"])
    for encoding in encodings:
        try:
            return body.decode(encoding, errors="replace")
        except LookupError:
            continue
    return body.decode("utf-8", errors="replace")


def safe_slug(value):
    value = clean_inline(value).lower()
    value = re.sub(r"https?://", "", value)
    value = re.sub(r"[^\w\u4e00-\u9fff.-]+", "-", value)
    value = re.sub(r"-{2,}", "-", value).strip(".-")
    return value or "clipy"


def yaml_value(value):
    value = str(value or "").replace("\\", "\\\\").replace('"', '\\"')
    return f'"{value}"'


def now_ms():
    return int(time.time() * 1000)
