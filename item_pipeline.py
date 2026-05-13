#!/usr/bin/env python3
from html import escape
from pathlib import Path
from urllib.parse import urlparse
import json
import mimetypes
import re
import shutil
import time
import zipfile
import xml.etree.ElementTree as ET

try:
    import fitz
except Exception:  # pragma: no cover - optional local dependency
    fitz = None


ITEMS_DIR_NAME = "items"
MAX_TEXT_CHARS = 400_000
MAX_SHEET_ROWS = 500
XML_NS = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
}


def build_item_for_entry(entry, data_dir):
    data_dir = Path(data_dir)
    entry = dict(entry)
    item_dir = data_dir / ITEMS_DIR_NAME / entry["id"]
    assets_dir = item_dir / "assets"
    item_dir.mkdir(parents=True, exist_ok=True)
    assets_dir.mkdir(parents=True, exist_ok=True)
    clear_directory(assets_dir)

    if entry.get("kind") == "link":
        content, parse_info = build_link_content(entry, data_dir, assets_dir)
    elif entry.get("kind") == "file":
        content, parse_info = build_single_file_content(entry, data_dir)
    elif entry.get("kind") == "file-batch":
        content, parse_info = build_file_batch_content(entry, data_dir)
    else:
        content, parse_info = build_basic_content(entry), {"status": "unsupported", "method": "none"}

    content = normalize_content(content)
    metadata = build_metadata(entry, parse_info, data_dir)
    content_path = item_dir / "content.md"
    snapshot_path = item_dir / "snapshot.html"
    metadata_path = item_dir / "metadata.json"

    content_path.write_text(content, encoding="utf-8")
    snapshot_path.write_text(raw_link_snapshot(entry, data_dir) or markdown_to_html_document(entry, content), encoding="utf-8")
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")

    entry["item"] = {
        "status": parse_info.get("status", "ready"),
        "contentPath": str(content_path.relative_to(data_dir)),
        "metadataPath": str(metadata_path.relative_to(data_dir)),
        "snapshotPath": str(snapshot_path.relative_to(data_dir)),
        "assetsPath": str(assets_dir.relative_to(data_dir)),
        "updatedAt": now_ms(),
    }
    return entry


def remove_entry_item(entry, data_dir):
    entry_id = entry.get("id") if isinstance(entry, dict) else str(entry or "")
    if not entry_id:
        return
    item_dir = Path(data_dir) / ITEMS_DIR_NAME / entry_id
    if item_dir.exists() and item_dir.is_dir():
        shutil.rmtree(item_dir)


def clear_directory(directory):
    for child in directory.iterdir():
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()


def build_items_for_entries(entries, data_dir):
    updated_entries = []
    reports = []
    for entry in entries:
        updated = build_item_for_entry(entry, data_dir)
        updated_entries.append(updated)
        reports.append(
            {
                "id": updated.get("id"),
                "kind": updated.get("kind"),
                "title": updated.get("title"),
                "status": updated.get("item", {}).get("status"),
                "contentPath": updated.get("item", {}).get("contentPath"),
            }
        )
    write_items_index(updated_entries, data_dir)
    return updated_entries, reports


def write_items_index(entries, data_dir):
    data_dir = Path(data_dir)
    index = []
    for entry in entries:
        item = entry.get("item") or {}
        if not item:
            continue
        index.append(
            {
                "id": entry.get("id"),
                "kind": entry.get("kind"),
                "sourceKey": entry.get("sourceKey"),
                "title": entry.get("title"),
                "createdAt": entry.get("createdAt"),
                "contentPath": item.get("contentPath"),
                "metadataPath": item.get("metadataPath"),
                "snapshotPath": item.get("snapshotPath"),
                "status": item.get("status"),
            }
        )
    index_path = data_dir / ITEMS_DIR_NAME / "index.json"
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")


def build_metadata(entry, parse_info, data_dir):
    files = entry_files(entry)
    metadata = {
        "id": entry.get("id"),
        "kind": entry.get("kind"),
        "title": entry.get("title"),
        "sourceKey": entry.get("sourceKey"),
        "sourceName": entry.get("sourceName"),
        "url": entry.get("url", ""),
        "createdAt": entry.get("createdAt"),
        "createdAtText": format_ms(entry.get("createdAt")),
        "updatedAt": now_ms(),
        "updatedAtText": format_ms(now_ms()),
        "parse": parse_info,
        "storage": {
            "contentMarkdown": f"{ITEMS_DIR_NAME}/{entry.get('id')}/content.md",
            "snapshotHtml": f"{ITEMS_DIR_NAME}/{entry.get('id')}/snapshot.html",
            "assetsDir": f"{ITEMS_DIR_NAME}/{entry.get('id')}/assets",
        },
    }
    if files:
        metadata["files"] = [
            {
                "id": file.get("id") or entry.get("id"),
                "fileName": file.get("fileName"),
                "fileType": file.get("fileType"),
                "fileSize": file.get("fileSize"),
                "filePath": file.get("filePath"),
                "filePathFromLibrary": file.get("filePath"),
                "exists": (Path(data_dir) / (file.get("filePath") or "")).exists(),
            }
            for file in files
        ]
    if entry.get("content"):
        metadata["legacyContent"] = entry.get("content")
    return metadata


def raw_link_snapshot(entry, data_dir):
    if entry.get("kind") != "link":
        return ""
    html_path = (entry.get("content") or {}).get("htmlPath")
    if not html_path:
        return ""
    path = (Path(data_dir) / html_path).resolve()
    try:
        data_root = Path(data_dir).resolve()
        if data_root not in path.parents or not path.exists():
            return ""
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


def build_link_content(entry, data_dir, assets_dir):
    old_content = entry.get("content") or {}
    markdown = ""
    parse_info = {
        "status": old_content.get("status") or "empty",
        "method": old_content.get("method") or "legacy_content_pipeline",
        "format": "markdown",
    }

    markdown_path = old_content.get("markdownPath")
    if markdown_path:
        source_path = Path(data_dir) / markdown_path
        if source_path.exists():
            markdown = source_path.read_text(encoding="utf-8")
            markdown = strip_front_matter(markdown)
            migrate_markdown_assets(markdown, entry, data_dir, assets_dir)
            markdown = rewrite_asset_links(markdown, entry.get("id"))
            parse_info["status"] = old_content.get("status") or "ready"

    if not markdown:
        reason = old_content.get("reason") or "没有可保存正文"
        markdown = "\n".join(
            [
                f"# {entry.get('title') or '未命名链接'}",
                "",
                f"- 来源：{entry.get('sourceName') or entry.get('sourceKey') or '网页'}",
                f"- 原链接：{entry.get('url') or ''}",
                f"- 抓取状态：{parse_info['status']}",
                f"- 说明：{reason}",
                "",
                entry.get("rawText") or "",
            ]
        )

    markdown = prepend_front_matter(entry, markdown, parse_info)
    return markdown, parse_info


def migrate_markdown_assets(markdown, entry, data_dir, assets_dir):
    old_assets_path = (entry.get("content") or {}).get("assetsPath")
    if not old_assets_path:
        return
    source_dir = Path(data_dir) / old_assets_path
    if not source_dir.exists() or not source_dir.is_dir():
        return
    for source_file in source_dir.iterdir():
        if source_file.is_file():
            shutil.copy2(source_file, assets_dir / source_file.name)


def rewrite_asset_links(markdown, entry_id):
    markdown = markdown.replace(f"../markdown_assets/{entry_id}/", "assets/")
    markdown = markdown.replace(f"markdown_assets/{entry_id}/", "assets/")
    return markdown


def build_single_file_content(entry, data_dir):
    file_result = parse_file_record(entry, data_dir)
    content = build_file_markdown(entry, [file_result])
    return content, aggregate_file_parse_info([file_result])


def build_file_batch_content(entry, data_dir):
    results = [parse_file_record(file, data_dir) for file in entry.get("files", [])]
    content = build_file_markdown(entry, results)
    return content, aggregate_file_parse_info(results)


def parse_file_record(file_record, data_dir):
    file_path = Path(data_dir) / (file_record.get("filePath") or "")
    file_name = file_record.get("fileName") or file_path.name or "未命名文件"
    file_type = file_record.get("fileType") or mimetypes.guess_type(file_name)[0] or "未知类型"
    kind = classify_file_kind(file_name, file_type)
    result = {
        "id": file_record.get("id"),
        "fileName": file_name,
        "fileType": file_type,
        "fileSize": file_record.get("fileSize", 0),
        "filePath": file_record.get("filePath"),
        "kind": kind,
        "status": "ready",
        "method": "",
        "text": "",
        "error": "",
    }

    if not file_path.exists():
        result.update({"status": "missing", "method": "filesystem", "error": "本地文件副本不存在"})
        return result

    try:
        if kind == "pdf":
            result["method"] = "pymupdf"
            result["text"] = extract_pdf_text(file_path)
        elif kind == "word":
            result["method"] = "docx_zip_xml"
            result["text"] = extract_docx_text(file_path)
        elif kind == "ppt":
            result["method"] = "pptx_zip_xml"
            result["text"] = extract_pptx_text(file_path)
        elif kind == "excel":
            result["method"] = "xlsx_zip_xml"
            result["text"] = extract_xlsx_text(file_path)
        elif kind == "image":
            result["method"] = "image_metadata_only"
            result["status"] = "metadata_only"
            result["text"] = "图片文件已保存为本地副本；OCR 和图片理解后续接入。"
        else:
            result["method"] = "unsupported_file_type"
            result["status"] = "unsupported"
            result["text"] = "该文件类型暂未接入正文解析。"
    except Exception as error:
        result["status"] = "failed"
        result["error"] = str(error)[:200]

    result["text"] = clamp_text(clean_text(result.get("text", "")))
    result["charCount"] = len(result["text"])
    return result


def build_file_markdown(entry, file_results):
    title = entry.get("title") or (file_results[0].get("fileName") if file_results else "文件")
    parse_info = aggregate_file_parse_info(file_results)
    lines = [
        "# " + title,
        "",
        f"- 类型：{entry.get('kind')}",
        f"- 文件数：{len(file_results)}",
        f"- 解析状态：{parse_info['status']}",
        f"- 保存时间：{format_ms(entry.get('createdAt'))}",
        "",
        "## 文件清单",
        "",
    ]
    for result in file_results:
        lines.append(
            f"- `{result.get('fileName')}` · {friendly_kind(result.get('kind'))} · "
            f"{result.get('status')} · `{result.get('filePath')}`"
        )

    for index, result in enumerate(file_results, start=1):
        lines.extend(["", f"## 文件 {index}: {result.get('fileName')}", ""])
        lines.extend(
            [
                f"- 类型：{friendly_kind(result.get('kind'))}",
                f"- MIME：{result.get('fileType')}",
                f"- 本地副本：`{result.get('filePath')}`",
                f"- 解析方法：{result.get('method')}",
                f"- 解析状态：{result.get('status')}",
            ]
        )
        if result.get("error"):
            lines.append(f"- 错误：{result['error']}")
        if result.get("text"):
            lines.extend(["", "### 提取文本", "", result["text"]])

    return prepend_front_matter(entry, "\n".join(lines), parse_info)


def build_basic_content(entry):
    return prepend_front_matter(
        entry,
        "\n".join(["# " + (entry.get("title") or "未命名收藏"), "", "该 entry 类型暂未接入知识库解析。"]),
        {"status": "unsupported", "method": "none"},
    )


def aggregate_file_parse_info(results):
    statuses = [result.get("status") for result in results]
    if not results:
        status = "empty"
    elif all(item == "ready" for item in statuses):
        status = "ready"
    elif any(item == "ready" for item in statuses):
        status = "partial"
    elif any(item == "metadata_only" for item in statuses):
        status = "metadata_only"
    elif any(item == "failed" for item in statuses):
        status = "failed"
    else:
        status = statuses[0] or "unsupported"
    return {
        "status": status,
        "method": "file_parser",
        "fileCount": len(results),
        "readyCount": sum(1 for result in results if result.get("status") == "ready"),
        "charCount": sum(result.get("charCount", 0) for result in results),
        "methods": sorted({result.get("method") for result in results if result.get("method")}),
    }


def extract_pdf_text(path):
    if fitz is None:
        raise RuntimeError("PyMuPDF/fitz not available")
    lines = []
    with fitz.open(str(path)) as document:
        for index, page in enumerate(document, start=1):
            text = clean_text(page.get_text("text"))
            if text:
                lines.extend([f"### Page {index}", "", text, ""])
    return "\n".join(lines)


def extract_docx_text(path):
    with zipfile.ZipFile(path) as archive:
        xml = archive.read("word/document.xml")
    root = ET.fromstring(xml)
    lines = []
    for paragraph in root.findall(".//w:p", XML_NS):
        text = "".join(node.text or "" for node in paragraph.findall(".//w:t", XML_NS))
        text = clean_text(text)
        if text:
            lines.append(text)
    return "\n\n".join(lines)


def extract_pptx_text(path):
    with zipfile.ZipFile(path) as archive:
        slide_names = sorted(
            (name for name in archive.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)),
            key=lambda item: int(re.search(r"slide(\d+)\.xml", item).group(1)),
        )
        sections = []
        for slide_index, slide_name in enumerate(slide_names, start=1):
            root = ET.fromstring(archive.read(slide_name))
            texts = [node.text or "" for node in root.findall(".//a:t", XML_NS)]
            text = clean_text("\n".join(texts))
            if text:
                sections.extend([f"### Slide {slide_index}", "", text, ""])
    return "\n".join(sections)


def extract_xlsx_text(path):
    with zipfile.ZipFile(path) as archive:
        shared_strings = read_xlsx_shared_strings(archive)
        sheet_map = read_xlsx_sheet_map(archive)
        sections = []
        for sheet_name, sheet_path in sheet_map:
            if sheet_path not in archive.namelist():
                continue
            rows = read_xlsx_sheet_rows(archive, sheet_path, shared_strings)
            if not rows:
                continue
            sections.extend([f"### Sheet: {sheet_name}", ""])
            for row in rows[:MAX_SHEET_ROWS]:
                sections.append("| " + " | ".join(escape_markdown_cell(cell) for cell in row) + " |")
            if len(rows) > MAX_SHEET_ROWS:
                sections.append(f"\n_只显示前 {MAX_SHEET_ROWS} 行，原表共 {len(rows)} 行。_")
            sections.append("")
    return "\n".join(sections)


def read_xlsx_shared_strings(archive):
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    values = []
    for item in root.findall(".//x:si", XML_NS):
        values.append("".join(node.text or "" for node in item.findall(".//x:t", XML_NS)))
    return values


def read_xlsx_sheet_map(archive):
    workbook = ET.fromstring(archive.read("xl/workbook.xml"))
    rels = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    rel_by_id = {
        rel.attrib.get("Id"): rel.attrib.get("Target", "")
        for rel in rels.findall(".//rel:Relationship", XML_NS)
    }
    sheets = []
    for sheet in workbook.findall(".//x:sheet", XML_NS):
        name = sheet.attrib.get("name", "Sheet")
        rel_id = sheet.attrib.get(f"{{{XML_NS['r']}}}id")
        target = rel_by_id.get(rel_id, "")
        if target:
            sheets.append((name, "xl/" + target.lstrip("/")))
    return sheets


def read_xlsx_sheet_rows(archive, sheet_path, shared_strings):
    root = ET.fromstring(archive.read(sheet_path))
    rows = []
    for row in root.findall(".//x:row", XML_NS):
        values = []
        for cell in row.findall("x:c", XML_NS):
            values.append(read_xlsx_cell(cell, shared_strings))
        if any(value for value in values):
            rows.append(values)
    return rows


def read_xlsx_cell(cell, shared_strings):
    cell_type = cell.attrib.get("t")
    value_node = cell.find("x:v", XML_NS)
    if cell_type == "inlineStr":
        return clean_inline("".join(node.text or "" for node in cell.findall(".//x:t", XML_NS)))
    if value_node is None or value_node.text is None:
        return ""
    value = value_node.text
    if cell_type == "s":
        try:
            return clean_inline(shared_strings[int(value)])
        except Exception:
            return ""
    return clean_inline(value)


def prepend_front_matter(entry, markdown, parse_info):
    body = strip_front_matter(markdown).strip()
    source_name = entry.get("sourceName") or entry.get("sourceKey") or ""
    lines = [
        "---",
        f"id: {yaml_value(entry.get('id', ''))}",
        f"kind: {yaml_value(entry.get('kind', ''))}",
        f"source: {yaml_value(source_name)}",
        f"source_key: {yaml_value(entry.get('sourceKey', ''))}",
        f"url: {yaml_value(entry.get('url', ''))}",
        f"title: {yaml_value(entry.get('title', ''))}",
        f"created_at: {yaml_value(format_ms(entry.get('createdAt')))}",
        f"parse_status: {yaml_value(parse_info.get('status', ''))}",
        f"parse_method: {yaml_value(parse_info.get('method', ''))}",
        "---",
        "",
    ]
    return "\n".join(lines) + body + "\n"


def markdown_to_html_document(entry, markdown):
    title = entry.get("title") or "Clipy Snapshot"
    body = markdown_to_html(strip_front_matter(markdown))
    source_url = entry.get("url") or ""
    source_link = f'<p class="source"><a href="{escape(source_url)}">{escape(source_url)}</a></p>' if source_url else ""
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(title)}</title>
  <style>
    body {{ margin: 0; background: #f7f4ef; color: #1e2528; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", sans-serif; }}
    main {{ max-width: 860px; margin: 0 auto; padding: 40px 24px 72px; background: #fffdf9; min-height: 100vh; }}
    h1, h2, h3 {{ line-height: 1.25; }}
    p, li {{ line-height: 1.7; }}
    img {{ max-width: 100%; border-radius: 8px; }}
    code {{ background: #f1ece4; padding: 0.1em 0.35em; border-radius: 4px; }}
    pre {{ overflow: auto; padding: 12px; background: #f1ece4; border-radius: 8px; }}
    .source {{ color: #6d7479; word-break: break-all; }}
  </style>
</head>
<body>
<main>
{source_link}
{body}
</main>
</body>
</html>
"""


def markdown_to_html(markdown):
    html_lines = []
    in_list = False
    for raw_line in markdown.splitlines():
        line = raw_line.rstrip()
        if not line:
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            continue
        heading = re.match(r"^(#{1,6})\s+(.*)", line)
        if heading:
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            level = len(heading.group(1))
            html_lines.append(f"<h{level}>{inline_markdown(heading.group(2))}</h{level}>")
            continue
        bullet = re.match(r"^[-*]\s+(.*)", line)
        if bullet:
            if not in_list:
                html_lines.append("<ul>")
                in_list = True
            html_lines.append(f"<li>{inline_markdown(bullet.group(1))}</li>")
            continue
        if in_list:
            html_lines.append("</ul>")
            in_list = False
        html_lines.append(f"<p>{inline_markdown(line)}</p>")
    if in_list:
        html_lines.append("</ul>")
    return "\n".join(html_lines)


def inline_markdown(text):
    escaped = escape(text)
    image_match = re.fullmatch(r"!\[(.*?)\]\((.*?)\)", text.strip())
    if image_match:
        alt = escape(image_match.group(1))
        src = escape(image_match.group(2))
        return f'<img src="{src}" alt="{alt}">'
    escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
    escaped = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', escaped)
    return escaped


def classify_file_kind(file_name, file_type):
    name = (file_name or "").lower()
    file_type = (file_type or "").lower()
    if file_type.startswith("image/") or re.search(r"\.(png|jpe?g|gif|webp|heic|bmp|tiff?)$", name):
        return "image"
    if "pdf" in file_type or name.endswith(".pdf"):
        return "pdf"
    if "spreadsheet" in file_type or "excel" in file_type or re.search(r"\.(xlsx|xlsm|csv)$", name):
        return "excel"
    if "presentation" in file_type or "powerpoint" in file_type or re.search(r"\.(pptx|pptm)$", name):
        return "ppt"
    if "word" in file_type or "document" in file_type or re.search(r"\.(docx|docm)$", name):
        return "word"
    return "file"


def entry_files(entry):
    if entry.get("kind") == "file":
        return [entry]
    if entry.get("kind") == "file-batch":
        return entry.get("files") or []
    return []


def strip_front_matter(markdown):
    return re.sub(r"^---\n.*?\n---\n?", "", markdown or "", flags=re.S).strip()


def normalize_content(content):
    return (content or "").strip() + "\n"


def clean_text(value):
    value = value or ""
    value = value.replace("\r", "\n")
    value = re.sub(r"\n[ \t]+", "\n", value)
    value = re.sub(r"[ \t]{2,}", " ", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def clean_inline(value):
    return re.sub(r"\s+", " ", clean_text(value)).strip()


def clamp_text(text):
    if len(text) <= MAX_TEXT_CHARS:
        return text
    return text[:MAX_TEXT_CHARS].rstrip() + "\n\n_内容过长，已截断。_"


def escape_markdown_cell(value):
    return str(value or "").replace("|", "\\|").replace("\n", " ")


def friendly_kind(kind):
    return {
        "pdf": "PDF",
        "word": "Word",
        "ppt": "PowerPoint",
        "excel": "Excel",
        "image": "图片",
        "file": "文件",
    }.get(kind or "", kind or "文件")


def format_ms(value):
    try:
        timestamp = int(value) / 1000
    except Exception:
        timestamp = time.time()
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(timestamp))


def now_ms():
    return int(time.time() * 1000)


def yaml_value(value):
    value = str(value or "")
    return json.dumps(value, ensure_ascii=False)
