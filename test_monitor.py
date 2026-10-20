import contextlib
import io
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import monitor


def record(identifier, title, kind="普通公告", purchaser="总行"):
    return dict(newsId=identifier, title=title, newsTypeCn=kind, purchaser=purchaser,
                publishTime=int(datetime.now(ZoneInfo("Asia/Shanghai")).timestamp() * 1000))


def response(rows):
    obj = MagicMock(ok=True)
    obj.json.return_value = {"data": {"pageModel": {"dataList": rows}}}
    return obj


class MonitorTests(unittest.TestCase):
    def test_reported_notice_and_other_survey_are_included_and_deduplicated(self):
        rows = [record("energy", "交通银行张江园区节能改造项目市场调研公告"),
                record("thermal", "交通银行2026年度张江数据中心机房翻新改造项目-智能热通道气流优化设备市场调研公告"),
                record("other", "总行系统市场调研公告"),
                record("branch", "其他分行系统市场调研公告", purchaser="上海分行")]
        items = monitor.records_to_items(rows + rows)
        self.assertEqual({x["news_id"] for x in items}, {"energy", "thermal", "other"})
        self.assertEqual(next(x for x in items if x["news_id"] == "energy")["categories"],
                         ["zhangjiang", "head_office"])

    def test_pagination_reaches_notice_on_later_page(self):
        page = MagicMock()
        nxt = MagicMock()
        nxt.count.return_value = 1
        nxt.get_attribute.side_effect = ["ant-pagination-next", None,
                                        "ant-pagination-disabled ant-pagination-next"]
        page.get_by_role.return_value = nxt
        later = record("energy", "交通银行张江园区节能改造项目市场调研公告")
        page.expect_response.return_value.__enter__.return_value.value = response([later])
        with patch("playwright.sync_api.expect"):
            rows = monitor.collect_pages(page, [record("first", "其他公告")], "测试")
        self.assertEqual([r["newsId"] for r in rows], ["first", "energy"])
        nxt.click.assert_called_once()

    def test_repeated_page_and_bad_payload_fail_instead_of_no_news(self):
        page = MagicMock()
        nxt = page.get_by_role.return_value
        nxt.count.return_value = 1
        nxt.get_attribute.return_value = "ant-pagination-next"
        rows = [record("first", "其他公告")]
        page.expect_response.return_value.__enter__.return_value.value = response(rows)
        with patch("playwright.sync_api.expect"), self.assertRaisesRegex(RuntimeError, "重复"):
            monitor.collect_pages(page, rows, "测试")
        bad = response([])
        bad.json.return_value = {"data": None}
        with self.assertRaisesRegex(RuntimeError, "结构异常"):
            monitor.parse_list_response(bad)

    def test_all_notices_are_batched_under_wecom_byte_limit(self):
        items = monitor.records_to_items([record(str(i), "张江" + "节" * 178) for i in range(25)])
        batches = monitor.message_batches(items, items)
        self.assertEqual([x for batch in batches for x in batch], items)
        self.assertTrue(all(len(monitor.make_message(items, batch, None).encode()) <= 1900
                            for batch in batches))

    def test_failed_batch_does_not_mark_unsent_ids_and_dry_run_has_no_side_effect(self):
        items = monitor.records_to_items([record(str(i), "张江" + "节" * 178) for i in range(5)])
        batches = monitor.message_batches(items, items)
        self.assertGreater(len(batches), 1)
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "seen.json"
            with patch.object(monitor, "STATE_PATH", state), patch.object(monitor, "scrape_portal", return_value=items), \
                    patch.object(monitor, "send_wecom", side_effect=[None, RuntimeError("失败"), None]), \
                    patch("sys.argv", ["monitor.py"]), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(monitor.main(), 1)
                self.assertEqual(monitor.read_state(), {x["id"] for x in batches[0]})
            with patch.object(monitor, "STATE_PATH", state), patch.object(monitor, "scrape_portal", return_value=items), \
                    patch.object(monitor, "send_wecom") as send, patch.object(monitor, "write_state") as write, \
                    patch("sys.argv", ["monitor.py", "--dry-run"]), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(monitor.main(), 0)
                send.assert_not_called()
                write.assert_not_called()


if __name__ == "__main__":
    unittest.main()
