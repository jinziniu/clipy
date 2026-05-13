#!/usr/bin/env python3
from pathlib import Path
import json

from item_pipeline import build_items_for_entries


ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "clipy_library"
BOOKMARKS_FILE = DATA_DIR / "bookmarks.json"


def main():
    if not BOOKMARKS_FILE.exists():
        raise SystemExit("clipy_library/bookmarks.json 不存在")

    entries = json.loads(BOOKMARKS_FILE.read_text(encoding="utf-8"))
    if not isinstance(entries, list):
        raise SystemExit("bookmarks.json 不是 entry 列表")

    updated_entries, reports = build_items_for_entries(entries, DATA_DIR)
    temp_file = BOOKMARKS_FILE.with_suffix(".tmp")
    temp_file.write_text(json.dumps(updated_entries, ensure_ascii=False, indent=2), encoding="utf-8")
    temp_file.replace(BOOKMARKS_FILE)

    print(f"rebuilt {len(reports)} items")
    for report in reports:
        print(
            f"- {report.get('id')} | {report.get('kind')} | "
            f"{report.get('status')} | {report.get('title')}"
        )


if __name__ == "__main__":
    main()
