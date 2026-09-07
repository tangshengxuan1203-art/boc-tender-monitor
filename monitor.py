"""Daily tender monitor for the Bank of Communications supplier portal."""

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
KEYWORDS = tuple(
    word.strip()
    for word in os.getenv("TARGET_KEYWORDS", "张江,张江园区").split(",")
    if word.strip()
)
MAX_ITEMS_IN_MESSAGE = 8


def now_shanghai() -> str:
    return datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M CST")


def normalize(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def scrape_portal() -> list[dict[str, str]]:
    """Return unique announcement-like links whose nearby text matches the keywords."""
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
            # The portal is a single-page application. Read anchors and their
            # nearest listing container after its JavaScript has rendered.
            raw_items = page.locator("a").evaluate_all(
                """anchors => anchors.map(a => {
                    const container = a.closest('tr, li, article, .item, .list-item, .notice-item')
                        || a.parentElement || a;
                    return {
                      title: (a.innerText || a.textContent || '').trim(),
                      href: a.href || '',
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

    candidates: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in raw_items:
        title = normalize(item.get("title", ""))
        href = item.get("href", "")
        context = normalize(item.get("context", ""))
        combined = f"{title} {context}"
        if not title or len(title) < 6 or not any(word in combined for word in KEYWORDS):
            continue
        # Avoid navigation/footer links; retain links with a listing-sized context.
        if len(context) < 10 or len(context) > 3000:
            continue
        url = urljoin(PORTAL_URL, href)
        identity = hashlib.sha256(f"{title}|{url}|{context[:500]}".encode()).hexdigest()
        if identity in seen:
            continue
        seen.add(identity)
        candidates.append(
            {
                "id": identity,
                "title": title[:180],
                "url": url,
                "context": context[:500],
            }
        )

    # A failed dynamic rendering should not silently be reported as “no notices”.
    if not candidates and any(word in body for word in KEYWORDS):
        raise RuntimeError("页面含目标关键词，但未识别出可追踪的公告链接，请检查页面结构")
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
                "keywords": list(KEYWORDS),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def make_message(items: list[dict[str, str]], new_items: list[dict[str, str]], error: str | None) -> str:
    header = f"【交行张江招标公告监控】\n监测时间：{now_shanghai()}"
    if error:
        return f"{header}\n⚠️ 本次监测异常：{error}\n请稍后查看 GitHub Actions 日志。"
    if new_items:
        lines = [f"✅ 发现 {len(new_items)} 条新的张江相关公告（当前匹配 {len(items)} 条）："]
        for item in new_items[:MAX_ITEMS_IN_MESSAGE]:
            lines.extend([f"• {item['title']}", item["url"]])
        if len(new_items) > MAX_ITEMS_IN_MESSAGE:
            lines.append(f"其余 {len(new_items) - MAX_ITEMS_IN_MESSAGE} 条请查看运行日志。")
        return "\n".join([header, *lines])
    return f"{header}\n✅ 本次未发现新的张江相关招标公告（当前匹配 {len(items)} 条）。"


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
