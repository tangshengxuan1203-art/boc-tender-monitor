"""Daily tender monitor for the Bank of Communications supplier portal."""

# Beijing verification run trigger.


from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import requests
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

PORTAL_URL = "https://bocom-gys.bankcomm.com/espuser/register/noticePage"
STATE_PATH = Path("data/seen.json")
MAX_ITEMS_IN_MESSAGE = 10

# Keep Zhangjiang monitoring and additionally monitor head-office procurement notices.
TARGETS = (
    {"key": "zhangjiang", "label": "张江园区", "keywords": ("张江", "张江园区")},
    {"key": "head_office", "label": "总行采购", "keywords": ("总行",)},
    {"key": "beijing_branch_test", "label": "北京分行（测试）", "keywords": ("北京分行",)},
)
PROCUREMENT_MARKERS = ("采购公告", "招标公告", "招标", "采购项目", "竞争性磋商", "询价", "单一来源")


def now_shanghai() -> str:
    return datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M CST")


def normalize(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def categories_for(text: str) -> list[str]:
    categories: list[str] = []
    for target in TARGETS:
        if not any(keyword in text for keyword in target["keywords"]):
            continue
        # Head-office matches are intentionally limited to procurement-style notices.
        if target["key"] == "head_office" and not any(marker in text for marker in PROCUREMENT_MARKERS):
            continue
        categories.append(target["key"])
    return categories


def scrape_portal() -> list[dict[str, Any]]:
    """Extract rendered announcement links and categorize them."""
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(
            viewport={"width": 1440, "height": 1800},
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
        )
        try:
            page.goto(PORTAL_URL, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(5000)
            # The portal is a single-page application. Read rendered listing links,
            # plus the raw href so the notification can provide a detail URL.
            raw_items = page.locator("a").evaluate_all(
                """anchors => anchors.map(a => {
                    const container = a.closest('tr, li, article, .item, .list-item, .notice-item')
                        || a.parentElement || a;
                    return {
                      title: (a.innerText || a.textContent || '').trim(),
                      href: a.href || '',
                      rawHref: a.getAttribute('href') || '',
                      onclick: a.getAttribute('onclick') || '',
                      context: (container.innerText || container.textContent || '').trim()
                    };
                })"""
            )
            body = page.locator("body").inner_text(timeout=10000)
        except PlaywrightTimeoutError as exc:
            raise RuntimeError(f"门户访问超时：{exc}") from exc
        finally:
            browser.close()

    if "502 Bad Gateway" in body or "unsafe legacy renegotiation" in body:
        raise RuntimeError("门户当前返回网关或 TLS 错误")

    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw_items:
        title = normalize(item.get("title", ""))
        context = normalize(item.get("context", ""))
        combined = f"{title} {context}"
        categories = categories_for(combined)
        if not title or len(title) < 6 or not categories:
            continue
        if len(context) < 10 or len(context) > 3000:
            continue

        raw_href = (item.get("rawHref") or "").strip()
        resolved_href = (item.get("href") or "").strip()
        # Use the detail page whenever the listing provides a real URL. If the
        # portal renders a non-link JavaScript control, transparently fall back
        # to its official announcement page instead of inventing a deep link.
        if raw_href and not raw_href.lower().startswith(("javascript:", "#")):
            detail_url = urljoin(PORTAL_URL, raw_href)
        elif resolved_href and resolved_href != PORTAL_URL:
            detail_url = resolved_href
        else:
            detail_url = PORTAL_URL

        identity = hashlib.sha256(
            f"{title}|{detail_url}|{context[:500]}".encode()
        ).hexdigest()
        if identity in seen:
            continue
        seen.add(identity)
        candidates.append(
            {
                "id": identity,
                "title": title[:180],
                "detail_url": detail_url,
                "context": context[:500],
                "categories": categories,
            }
        )

    print(f"Scraped {len(raw_items)} rendered links; matched {len(candidates)} target announcements.")
    return candidates


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
            lines.extend((f"• {item['title']}", f"详情：{item['detail_url']}"))
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
        message = make_message(items, new_items, None)
        print(message)
        send_wecom(message)
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
