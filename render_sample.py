"""Render a current official portal notice as a publicly readable Markdown detail card."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from playwright.sync_api import sync_playwright

PORTAL_URL = "https://bocom-gys.bankcomm.com/espuser/register/noticePage"
LIST_API = "https://bocom-gys.bankcomm.com/espddw/api/news/notice/index/list"
OUTPUT = Path("notices/official-notice-sample.md")


def format_date(timestamp_ms: Any) -> str:
    try:
        return datetime.fromtimestamp(int(timestamp_ms) / 1000, ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OSError):
        return "未知"


def fetch_current_record() -> dict[str, Any]:
    payloads: list[dict[str, Any]] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1800})

        def capture(response: Any) -> None:
            if LIST_API not in response.url:
                return
            try:
                payloads.append(response.json())
            except Exception:
                pass

        page.on("response", capture)
        try:
            page.goto(PORTAL_URL, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(5000)
        finally:
            browser.close()

    records = [
        record
        for payload in payloads
        for record in payload.get("data", {}).get("pageModel", {}).get("dataList", [])
    ]
    record = next(
        (
            item
            for item in records
            if item.get("title")
            and "告知函" not in item.get("title", "")
            and "认定标准" not in item.get("title", "")
        ),
        None,
    )
    if not record:
        raise RuntimeError("官网列表接口未返回可用于样例的正式公告")
    return record


def main() -> None:
    record = fetch_current_record()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        "\n".join(
            (
                f"# {record['title']}",
                "",
                "> 公告详情卡：字段由交通银行智采平台供应商门户实时同步。",
                "",
                "| 字段 | 内容 |",
                "| --- | --- |",
                f"| 公告类型 | {record.get('newsTypeCn') or '公告'} |",
                f"| 发布时间 | {format_date(record.get('publishTime'))} |",
                f"| 采购人 | {record.get('purchaser') or '未标注'} |",
                f"| 公告编号 | {record.get('newsId')} |",
                "",
                "## 官方查询",
                "",
                f"请使用公告标题或公告编号在 [交通银行智采平台公告页]({PORTAL_URL}) 查询原始公告。",
                "",
            )
        ),
        encoding="utf-8",
    )
    print(f"Rendered {OUTPUT}: {record['title']}")


if __name__ == "__main__":
    main()
