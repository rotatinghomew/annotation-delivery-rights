"""检查本地交换资料的完整性和取件接口。"""
from hashlib import sha256
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from functools import partial
from pathlib import Path
from threading import Thread
import unittest
from urllib.request import urlopen

DATA = Path(__file__).resolve().parents[1] / "data"
FILE = DATA / "annotation_20261204.json"


def by_id(packet, group, key):
    return next(row for row in packet[group] if row["id"] == key)


class ExchangeTest(unittest.TestCase):
    def test_records(self):
        data = json.loads(FILE.read_text(encoding="utf-8"))
        for group, rows in data.items():
            with self.subTest(group=group):
                self.assertTrue(rows)
                self.assertEqual(len(rows), len({row["id"] for row in rows}))
                self.assertTrue(all(row.keys() == rows[0].keys() for row in rows))
        for item in data["fragments"]:
            material = by_id(data, "materials", item["asset"])["body"]
            self.assertLess(item["start"], item["end"])
            self.assertLessEqual(item["end"], len(material))
        revoked = by_id(data, "permissions", "P2")
        claim = by_id(data, "claims", "C1")
        self.assertLess(claim["requested"], revoked["withdrawn"])
        self.assertIn(revoked["fragment"], by_id(data, "work", "W1")["fragments"])
        self.assertNotIn(revoked["fragment"], by_id(data, "work", "W3")["fragments"])
        self.assertLess(by_id(data, "payments", "Y1")["paid_at"], by_id(data, "reviews", "Q5")["received"])

    def test_source_over_http(self):
        handler = partial(SimpleHTTPRequestHandler, directory=str(DATA))
        with ThreadingHTTPServer(("127.0.0.1", 0), handler) as server:
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                address = f"http://127.0.0.1:{server.server_port}/{FILE.name}"
                with urlopen(address, timeout=5) as reply:
                    self.assertEqual(reply.status, 200)
                    self.assertEqual(sha256(reply.read()).digest(), sha256(FILE.read_bytes()).digest())
            finally:
                server.shutdown()
                thread.join(timeout=5)
