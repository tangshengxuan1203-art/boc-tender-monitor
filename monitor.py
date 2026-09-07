"""Daily tender monitor for the Bank of Communications supplier portal."""


from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

PORTAL_URL = "https://bocom-gys.bankcomm.com/espuser/register/noticePage"
NOTICE_LIST_API = "https://bocom-gys.bankcomm.com/espddw/api/news/notice/index/list"
STATE_PATH = Path("data/seen.json")
MAX_ITEMS_IN_MESSAGE = 10

TARGETS = (
    {"key": "zhangjiang", "label": "张江园区"},
    {"key": "head_office", "label": "总行采购"},
)
HEAD_OFFICE_PROCUREMENT_TYPES = {"采购公告", "招标公告"}


def now_shanghai() -> str:
    return datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M CST")


def format_date(timestamp_ms: Any) -> str:
    try:
        return datetime.fromtimestamp(int(timestamp_ms) / 1000, ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OSError):
        return "未知"


def notice_categories(record: dict[str, Any]) -> list[str]:
    title = str(record.get("title", ""))
    categories: list[str] = []
    if "张江" in title:
        categories.append("zhangjiang")
    # The supplier portal returns the actual purchaser separately from the title.
    if (
        record.get("purchaser") == "总行"
        and record.get("newsTypeCn") in HEAD_OFFICE_PROCUREMENT_TYPES
    ):
        categories.append("head_office")
    return categories


def scrape_portal() -> list[dict[str, Any]]:
    """Read the portal's rendered list response and return target notices."""
    responses: list[dict[str, Any]] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(
            viewport={"width": 1440, "height": 1800},
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
        )

        def capture_list_response(response: Any) -> None:
            if NOTICE_LIST_API not in response.url:
                return
            try:
                responses.append(response.json())
            except Exception:
                pass

        page.on("response", capture_list_response)
        try:
            page.goto(PORTAL_URL, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(5000)
            body = page.locator("body").inner_text(timeout=10000)
        except PlaywrightTimeoutError as exc:
            raise RuntimeError(f"门户访问超时：{exc}") from exc
        finally:
            browser.close()

    if "502 Bad Gateway" in body or "unsafe legacy renegotiation" in body:
        raise RuntimeError("门户当前返回网关或 TLS 错误")
    if not responses:
        raise RuntimeError("未捕获到公告列表数据接口响应")

    records: list[dict[str, Any]] = []
    for response in responses:
        records.extend(response.get("data", {}).get("pageModel", {}).get("dataList", []))

    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for record in records:
        news_id = str(record.get("newsId", "")).strip()
        title = str(record.get("title", "")).strip()
        categories = notice_categories(record)
        if not news_id or not title or not categories or news_id in seen:
            continue
        seen.add(news_id)
        items.append(
            {
                "id": hashlib.sha256(news_id.encode()).hexdigest(),
                "news_id": news_id,
                "title": title[:180],
                "categories": categories,
                "notice_type": str(record.get("newsTypeCn", "公告")),
                "published_at": format_date(record.get("publishTime")),
            }
        )

    print(f"Read {len(records)} portal notices; matched {len(items)} target notices.")
    return items


def read_state() -> set[str]:
    if not STATE_PATH.exists():
        return set()
    try:
        return set(json.loads(STATE_PATH.read_text(encoding="utf-8")).get("seen_ids", []))
    except (json.JSONDecodeError, OSError) as exc:
        raise RuntimeError(f"状态文件无法读取：{exc}") from exc


def write_state(ids: set[str]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(
        json.dumps(
            {
                "seen_ids": sorted(ids)[-1000:],
                "updated_at": now_shanghai(),
                "monitors": [target["label"] for target in TARGETS],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def make_message(items: list[dict[str, Any]], new_items: list[dict[str, Any]], error: str | None) -> str:
    header = f"【交行采购公告监控】\n监测时间：{now_shanghai()}"
    if error:
        return f"{header}\n⚠️ 本次监测异常：{error}\n请稍后查看 GitHub Actions 日志。"

    counts = {
        target["key"]: sum(target["key"] in item["categories"] for item in items)
        for target in TARGETS
    }
    if not new_items:
        return (
            f"{header}\n✅ 本次未发现新增公告。"
            f"\n当前页匹配：张江园区 {counts['zhangjiang']} 条；总行采购 {counts['head_office']} 条。"
        )

    lines = [f"✅ 发现 {len(new_items)} 条新增公告："]
    for target in TARGETS:
        group = [item for item in new_items if target["key"] in item["categories"]]
        if not group:
            continue
        lines.append(f"\n【{target['label']}】{len(group)} 条")
        for item in group[:MAX_ITEMS_IN_MESSAGE]:
            lines.extend(
                (
                    f"• {item['title']}",
                    f"类型：{item['notice_type']}｜发布时间：{item['published_at']}",
                    f"公告编号：{item['news_id']}",
                    f"公告查询入口：{PORTAL_URL}",
                )
            )
        if len(group) > MAX_ITEMS_IN_MESSAGE:
            lines.append(f"其余 {len(group) - MAX_ITEMS_IN_MESSAGE} 条请查看运行日志。")
    return "\n".join((header, *lines))


def send_wecom(message: str) -> None:
    webhook = os.getenv("WECOM_WEBHOOK", "").strip()
    if not webhook:
        raise RuntimeError("缺少 GitHub Secret：WECOM_WEBHOOK")
    response = requests.post(
        webhook,
        json={"msgtype": "text", "text": {"content": message}},
        timeout=20,
    )
    response.raise_for_status()
    payload: dict[str, Any] = response.json()
    if payload.get("errcode") != 0:
        raise RuntimeError(f"企业微信机器人返回错误：{payload}")


def main() -> int:
    try:
        items = scrape_portal()
        old_ids = read_state()
        new_items = [item for item in items if item["id"] not in old_ids]
        send_wecom(make_message(items, new_items, None))
        write_state(old_ids | {item["id"] for item in items})
        print(f"Matched {len(items)} item(s); {len(new_items)} new item(s).")
        return 0
    except Exception as exc:
        message = make_message([], [], str(exc))
        print(message, file=sys.stderr)
        try:
            send_wecom(message)
        except Exception as notify_exc:
            print(f"企业微信异常通知失败：{notify_exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
