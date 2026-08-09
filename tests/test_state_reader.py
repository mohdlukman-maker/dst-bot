#!/usr/bin/env python3
"""Acceptance tests for lib/state_reader.py (Task 1)."""
import os
import sys
import json
import time
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from lib import state_reader


def write_klei(path, payload, header=True):
    text = ("KLEI     1 " if header else "") + json.dumps(payload)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


class TestStateReader(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = self._tmp.name
        self.path = os.path.join(self.dir, "dst_ai_bot_state")

    def test_valid_klei_current_timestamp(self):
        write_klei(self.path, {"health": [150, 150], "timestamp": int(time.time())})
        r = state_reader.read_state(self.dir)
        self.assertTrue(r["ok"])
        self.assertTrue(r["fresh"])
        self.assertEqual(r["reason"], "")
        self.assertEqual(r["state"]["health"], [150, 150])

    def test_stale_but_populated(self):
        write_klei(self.path, {"day": 4, "timestamp": int(time.time()) - 60})
        r = state_reader.read_state(self.dir)
        self.assertFalse(r["ok"])
        self.assertFalse(r["fresh"])
        self.assertEqual(r["reason"], "stale")
        self.assertEqual(r["state"]["day"], 4)  # data still usable

    def test_plain_json_no_header(self):
        write_klei(self.path, {"x": 1, "timestamp": int(time.time())}, header=False)
        r = state_reader.read_state(self.dir)
        self.assertTrue(r["ok"])
        self.assertEqual(r["state"]["x"], 1)

    def test_missing_file(self):
        r = state_reader.read_state(self.dir)  # nothing written
        self.assertFalse(r["ok"])
        self.assertEqual(r["reason"], "missing")

    def test_garbage_bytes(self):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("KLEI     1 {this is not json")
        r = state_reader.read_state(self.dir)
        self.assertFalse(r["ok"])
        self.assertEqual(r["reason"], "bad_json")

    def test_errors_preserved_ok_still_true(self):
        payload = {"day": 2, "timestamp": int(time.time()),
                   "_errors": {"nearby": "boom"}}
        write_klei(self.path, payload)
        r = state_reader.read_state(self.dir)
        self.assertTrue(r["ok"])                      # partial state usable
        self.assertEqual(r["errors"], {"nearby": "boom"})

    def test_newline_inside_string_parses(self):
        payload = {"note": "line1\nline2\nline3", "timestamp": int(time.time())}
        write_klei(self.path, payload)
        r = state_reader.read_state(self.dir)
        self.assertTrue(r["ok"])
        self.assertEqual(r["state"]["note"], "line1\nline2\nline3")

    def test_no_timestamp(self):
        write_klei(self.path, {"day": 1})
        r = state_reader.read_state(self.dir)
        self.assertFalse(r["ok"])
        self.assertFalse(r["fresh"])
        self.assertEqual(r["age_s"], -1.0)
        self.assertEqual(r["reason"], "no_timestamp")


if __name__ == "__main__":
    unittest.main(verbosity=2)
