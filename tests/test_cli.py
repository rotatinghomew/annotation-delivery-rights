"""联调入口测试：确认命令行只读查询不写回资料，且错误有非零退出码。"""
from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path
import unittest

from annotation_exchange.__main__ import run


DATA = Path(__file__).resolve().parents[1] / "data" / "annotation_20261204.json"


def invoke(*args):
    out, err = StringIO(), StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = run(["--data", str(DATA), *args])
    return code, out.getvalue(), err.getvalue()


class CliTest(unittest.TestCase):
    def test_issues_is_empty_json_array(self):
        code, out, err = invoke("issues")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), [])
        self.assertEqual(err, "")

    def test_deliver_reports_substitution_and_unrelated_work(self):
        code, out, _ = invoke("deliver", "C2")
        self.assertEqual(code, 0)
        report = json.loads(out)
        statuses = {item["requested"]: item["status"] for item in report["deliveries"]}
        self.assertEqual(statuses, {"W1": "substituted", "W3": "delivered", "W4": "delivered"})

    def test_revocation_reports_outstanding_ack(self):
        code, out, _ = invoke("revocation", "P2")
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["status"]["unacknowledged"], ["N1"])
        self.assertEqual(payload["impact"]["items"][0]["claim"], "C1")

    def test_reopen_by_original_id_fails(self):
        code, out, err = invoke(
            "reopen", "C2", "W1", "--grantee", "kitchen-team"
        )
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("PermissionDenied", err)

    def test_unknown_claim_is_reported(self):
        code, _, err = invoke("deliver", "C99")
        self.assertEqual(code, 1)
        self.assertIn("ExchangeError", err)

    def test_cli_does_not_modify_source_file(self):
        before = DATA.read_bytes()
        invoke("ack", "N1", "--grantee", "kitchen-team", "--claim", "C1", "--at", "16:00")
        self.assertEqual(DATA.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
