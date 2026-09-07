"""One-off random live-notice test for Enterprise WeChat."""
import os, random
from datetime import datetime
from zoneinfo import ZoneInfo
import requests
from playwright.sync_api import sync_playwright

PORTAL = "https://bocom-gys.bankcomm.com/espuser/register/noticePage"
API = "https://bocom-gys.bankcomm.com/espddw/api/news/notice/index/list"

def date(v):
    try: return datetime.fromtimestamp(int(v)/1000, ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d")
    except Exception: return "未知"

responses = []
with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    page = b.new_page(user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
    page.on("response", lambda r: responses.append(r.json()) if API in r.url else None)
    page.goto(PORTAL, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(5000)
    b.close()
records = [x for r in responses for x in r.get("data",{}).get("pageModel",{}).get("dataList",[]) if x.get("title") and x.get("newsId")]
if not records: raise RuntimeError("未抓到当前公告")
x = random.choice(records)
msg = "\n".join(("【交行采购公告监控｜随机抓取测试】", f"监测时间：{datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%d %H:%M CST')}", "✅ 已随机抓到 1 条当前公告：", f"• {x['title']}", f"类型：{x.get('newsTypeCn','公告')}｜发布时间：{date(x.get('publishTime'))}", f"采购人：{x.get('purchaser') or '未标注'}", f"公告编号：{x['newsId']}", f"官方查询入口：{PORTAL}"))
res = requests.post(os.environ["WECOM_WEBHOOK"], json={"msgtype":"text","text":{"content":msg}}, timeout=20)
res.raise_for_status()
if res.json().get("errcode") != 0: raise RuntimeError(res.text)
print(x["title"])
