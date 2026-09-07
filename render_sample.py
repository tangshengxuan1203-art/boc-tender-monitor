"""Render one official portal notice as a publicly readable Markdown page."""


from __future__ import annotations

import html
import json
import re
from pathlib import Path
from typing import Any

from playwright.sync_api import sync_playwright

NEWS_ID = "1ac43d5a34758fcff339c507dab3d7bfc799635417909b4bd2260b039fb18682114851abc0d16e9eae64eb75afd975ac"
PORTAL_URL = "https://bocom-gys.bankcomm.com/espuser/register/noticePage"
DETAIL_API_FRAGMENT = "/espddw/api/news/notice/index/details"
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
    value = html.unescape(value)
    value = re.sub(r"<br\\s*/?>", "\\n", value, flags=re.I)
    value = re.sub(r"</(p|div|li|tr|h[1-6])>", "\\n", value, flags=re.I)
    value = re.sub(r"<[^>]+>", "", value)
    return value.strip()


def fetch_detail() -> dict[str, Any]:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1800})
        try:
            page.goto(PORTAL_URL, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(1200)
            payload = page.evaluate(
                """async newsId => {
                    const response = await fetch('/espddw/api/news/notice/index/details', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({newsId}),
                    });
                    return await response.json();
                }""",
                NEWS_ID,
            )
        finally:
            browser.close()
    return payload.get("data", payload)


def main() -> None:
    data = fetch_detail()
    title = find_value(data, ("title", "newsTitle")) or "交通银行公告详情"
    news_id = find_value(data, ("newsId",))
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
                f"- 公告编号：{news_id or '未返回'}",
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
    print(json.dumps({"title": title, "news_id": news_id}, ensure_ascii=False))


if __name__ == "__main__":
    main()
