"""Daily tender monitor for the Bank of Communications supplier portal."""


from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

PORTAL_URL = "https://bocom-gys.bankcomm.com/espuser/register/noticePage"
NOTICE_LIST_API = "https://bocom-gys.bankcomm.com/espddw/api/news/notice/index/list"
STATE_PATH = Path("data/seen.json")
MAX_PAGES = 500
LOOKBACK_DAYS = 30

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
        and (record.get("newsTypeCn") in HEAD_OFFICE_PROCUREMENT_TYPES
             or "市场调研" in title or "征集" in title)
    ):
        categories.append("head_office")
    return categories


def parse_list_response(response: Any) -> list[dict[str, Any]]:
    if not response.ok:
        raise RuntimeError(f"公告接口 HTTP {response.status}")
    payload = response.json()
    try:
        rows = payload["data"]["pageModel"]["dataList"]
    except (KeyError, TypeError) as exc:
        raise RuntimeError("公告接口结构异常，不能视为无新增公告") from exc
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise RuntimeError("公告列表数据格式异常")
    return rows


def collect_pages(page: Any, first_records: list[dict[str, Any]], label: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    fingerprints: set[tuple[str, ...]] = set()
    rows = first_records
    for number in range(1, MAX_PAGES + 1):
        fingerprint = tuple(str(row.get("newsId", "")) for row in rows)
        if rows and fingerprint in fingerprints:
            raise RuntimeError(f"{label}第{number}页重复，分页未推进")
        fingerprints.add(fingerprint)
        records.extend(rows)
        print(f"Scan {label}: page={number}, records={len(rows)}", flush=True)
        next_button = page.get_by_role("listitem", name="下一页", exact=True)
        if next_button.count() != 1:
            raise RuntimeError(f"{label}分页控件缺失，无法确认扫描完整")
        if ("disabled" in (next_button.get_attribute("class") or "")
                or next_button.get_attribute("aria-disabled") == "true"):
            return records
        if not rows:
            raise RuntimeError(f"{label}非末页返回空数据")
        with page.expect_response(lambda response: NOTICE_LIST_API in response.url, timeout=45000) as pending:
            next_button.click()
        rows = parse_list_response(pending.value)
        # Wait for the UI to commit the newly received page before another click.
        from playwright.sync_api import expect
        expect(page.get_by_role("listitem", name=str(number + 1), exact=True)).to_have_attribute(
            "class", re.compile(".*ant-pagination-item-active.*"), timeout=10000
        )
    raise RuntimeError(f"{label}超过{MAX_PAGES}页上限，扫描未完成")


def records_to_items(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cutoff = (datetime.now(ZoneInfo("Asia/Shanghai")) - timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%d")
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for record in records:
        news_id = str(record.get("newsId") or "").strip()
        title = str(record.get("title") or "").strip()
        categories = notice_categories(record)
        published_at = format_date(record.get("publishTime"))
        # Zhangjiang has an independent all-date search. Other head-office notices
        # are backfilled over a rolling 30-day window to avoid flooding old history.
        if "zhangjiang" not in categories and published_at != "未知" and published_at < cutoff:
            continue
        if not news_id or not title or not categories or news_id in seen:
            continue
        seen.add(news_id)
        items.append({
            "id": hashlib.sha256(news_id.encode()).hexdigest(),
            "news_id": news_id,
            "title": title[:180],
            "categories": categories,
            "notice_type": str(record.get("newsTypeCn", "公告")),
            "published_at": published_at,
        })
    return sorted(items, key=lambda item: (item["published_at"], item["news_id"]), reverse=True)


def scrape_portal() -> list[dict[str, Any]]:
    """Search Zhangjiang independently, then scan every unfiltered list page."""
    records: list[dict[str, Any]] = []
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
            with page.expect_response(lambda response: NOTICE_LIST_API in response.url, timeout=60000) as pending:
                page.goto(PORTAL_URL, wait_until="domcontentloaded", timeout=60000)
            parse_list_response(pending.value)
            title_input = page.get_by_role("textbox", name="公告标题:", exact=True)
            for keyword, label in (("张江", "张江全量检索"), ("", "门户全量分页")):
                title_input.fill(keyword)
                with page.expect_response(lambda response: NOTICE_LIST_API in response.url, timeout=45000) as pending:
                    page.get_by_role("button", name="查 询").click()
                rows = parse_list_response(pending.value)
                records.extend(collect_pages(page, rows, label))
        except PlaywrightTimeoutError as exc:
            raise RuntimeError("门户访问或分页超时，本次扫描不完整") from exc
        finally:
            browser.close()

    items = records_to_items(records)
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
                "seen_ids": sorted(ids),
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
            f"\n完整分页扫描匹配：张江园区 {counts['zhangjiang']} 条；总行采购 {counts['head_office']} 条。"
        )

    lines = [f"✅ 本批新增公告 {len(new_items)} 条："]
    for item in new_items:
        labels = "、".join(target["label"] for target in TARGETS if target["key"] in item["categories"])
        lines.extend((
            f"\n【{labels}】{item['title']}",
            f"类型：{item['notice_type']}｜发布时间：{item['published_at']}",
            f"公告编号：{item['news_id']}",
            f"查询入口：{PORTAL_URL}",
        ))
    return "\n".join((header, *lines))


def message_batches(items: list[dict[str, Any]], new_items: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    batches: list[list[dict[str, Any]]] = []
    batch: list[dict[str, Any]] = []
    for item in new_items:
        candidate = batch + [item]
        if len(make_message(items, candidate, None).encode("utf-8")) > 1900:
            if not batch:
                raise RuntimeError("单条公告超过消息字节限制")
            batches.append(batch)
            candidate = [item]
            if len(make_message(items, candidate, None).encode("utf-8")) > 1900:
                raise RuntimeError("单条公告超过消息字节限制")
        batch = candidate
    if batch:
        batches.append(batch)
    return batches


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
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="只抓取并打印，不发送消息、不修改状态")
    args = parser.parse_args()
    try:
        items = scrape_portal()
        if args.dry_run:
            print(json.dumps(items, ensure_ascii=False, indent=2))
            return 0
        old_ids = read_state()
        new_items = [item for item in items if item["id"] not in old_ids]
        if not new_items:
            send_wecom(make_message(items, [], None))
            write_state(old_ids)
        for batch in message_batches(items, new_items):
            send_wecom(make_message(items, batch, None))
            old_ids.update(item["id"] for item in batch)
            write_state(old_ids)
        print(f"Matched {len(items)} item(s); {len(new_items)} new item(s).")
        return 0
    except Exception as exc:
        message = make_message([], [], str(exc))
        print(message, file=sys.stderr)
        if not args.dry_run:
            try:
                send_wecom(message)
            except Exception as notify_exc:
                print(f"企业微信异常通知失败：{notify_exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

