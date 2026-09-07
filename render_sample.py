"""Render one official portal notice as a publicly readable Markdown page."""

from __future__ import annotations

import html
import json
import re
from pathlib import Path
from typing import Any

import requests

NEWS_ID = "1ac43d5a34758fcff339c507dab3d7bfc799635417909b4bd2260b039fb18682114851abc0d16e9eae64eb75afd975ac"
DETAIL_API = "https://bocom-gys.bankcomm.com/espddw/api/news/notice/index/details"
PORTAL_URL = "https://bocom-gys.bankcomm.com/espuser/register/noticePage"
OUTPUT = Path("notices/beijing-branch-sample.md")


def find_value(value: Any, names: tuple[str, ...]) -> str:
    if isinstance(value, dict):
        for name in names:
            candidate = value.get(name)
            if isinstance(candidate, str) and candidate.strip():
                return candidate
        for child in value.values():
            found = find_value(child, names)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = find_value(child, names)
            if found:
                return found
    return ""


def to_plain_text(value: str) -> str:
    value = re.sub(r"<(script|style)[^>]*>.*?</\\1>", "", value, flags=re.I | re.S)
    value = re.sub(r"<br\\s*/?>", "\\n", value, flags=re.I)
    value = re.sub(r"</(p|div|li|tr|h[1-6])>", "\\n", value, flags=re.I)
    value = re.sub(r"<[^>]+>", "", value)
    return html.unescape(value).strip()


def main() -> None:
    response = requests.post(DETAIL_API, json={"newsId": NEWS_ID}, timeout=30)
    response.raise_for_status()
    payload = response.json()
    data = payload.get("data", payload)

    title = find_value(data, ("title", "newsTitle")) or "交通银行公告详情"
    content = find_value(
        data,
        ("content", "newsContent", "detailContent", "description", "contentText"),
    )
    content = to_plain_text(content) if content else "平台详情接口未返回可公开展示的正文。"
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        "\\n".join(
            (
                f"# {title}",
                "",
                "## 公告来源",
                "",
                "- 来源：交通银行智采平台供应商门户",
                f"- 公告编号：{NEWS_ID}",
                f"- 官方查询入口：{PORTAL_URL}",
                "",
                "## 公告正文",
                "",
                content,
                "",
            )
        ),
        encoding="utf-8",
    )
    print(f"Rendered {OUTPUT} ({len(content)} chars).")
    print(json.dumps({"top_level_keys": list(data) if isinstance(data, dict) else []}, ensure_ascii=False))


if __name__ == "__main__":
    main()
