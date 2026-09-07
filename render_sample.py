"""Render the current portal notice as a publicly readable Markdown page."""

from __future__ import annotations

import html
import json
import re
from pathlib import Path
from typing import Any

from playwright.sync_api import sync_playwright

PORTAL_URL = "https://bocom-gys.bankcomm.com/espuser/register/noticePage"
DETAIL_API_FRAGMENT = "/espddw/api/news/notice/index/details"
OUTPUT = Path("notices/official-notice-sample.md")


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


def fetch_current_detail() -> dict[str, Any]:
    payloads: list[dict[str, Any]] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1800})

        def capture(response: Any) -> None:
            if DETAIL_API_FRAGMENT not in response.url:
                return
            try:
                payloads.append(response.json())
            except Exception:
                pass

        page.on("response", capture)
        try:
            page.goto(PORTAL_URL, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(5000)
            titles = page.locator("a").all_inner_texts()
            selected_title = next(
                (
                    title.strip()
                    for title in titles
                    if "采购公告" in title or "招标公告" in title
                ),
                "",
            )
            if not selected_title:
                raise RuntimeError("当前页未找到可用于样例的采购/招标公告")
            page.locator("a").filter(has_text=selected_title).first.click()
            page.wait_for_timeout(1500)
        finally:
            browser.close()

    if not payloads:
        raise RuntimeError("未捕获到公告详情接口响应")
    return payloads[-1].get("data", payloads[-1])


def main() -> None:
    data = fetch_current_detail()
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
