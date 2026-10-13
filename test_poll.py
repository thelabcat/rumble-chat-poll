#!/usr/bin/env python3
"""Tests for vote tallying and for unblocking the chat SSE read."""

from __future__ import annotations

import importlib.util
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.machinery import SourceFileLoader
from pathlib import Path

import requests
import tkinter as tk
from requests_sse import EventSource

_MODULE_PATH = str(Path(__file__).with_name("rumble_chat_poll.pyw"))
_LOADER = SourceFileLoader("rumble_chat_poll", _MODULE_PATH)
_SPEC = importlib.util.spec_from_file_location("rumble_chat_poll", _MODULE_PATH, loader=_LOADER)
poll = importlib.util.module_from_spec(_SPEC)
_LOADER.exec_module(poll)


class BallotTests(unittest.TestCase):
    def test_exact_and_numeric_votes(self):
        ballot = poll.Ballot(["Red", "Blue"])
        self.assertEqual(ballot.cast(1, "Red"), "Red")
        self.assertEqual(ballot.cast(2, " 2 "), "Blue")
        self.assertIsNone(ballot.cast(1, "Blue"))
        snapshot = ballot.snapshot()
        self.assertEqual(snapshot["counts"], {"Red": 1, "Blue": 1})
        self.assertEqual(snapshot["leaders"], ("Red", "Blue"))

    def test_rejects_non_votes(self):
        ballot = poll.Ballot(["Red", "Blue"])
        for text in ("", "  ", "red", "0", "3", "01", "+1", "1.5", "²", "nope"):
            self.assertIsNone(ballot.choice_for(text), text)
        self.assertEqual(ballot.snapshot()["leaders"], ())

    def test_exact_numeric_text_beats_the_index(self):
        ballot = poll.Ballot(["1", "Banana"])
        self.assertEqual(ballot.choice_for("1"), "1")
        self.assertEqual(ballot.choice_for("2"), "Banana")

    def test_numeric_can_be_disabled(self):
        ballot = poll.Ballot(["Red", "Blue"], numeric=False)
        self.assertIsNone(ballot.choice_for("1"))
        self.assertEqual(ballot.choice_for("Red"), "Red")

    def test_winner_and_tie_text(self):
        single = {"counts": {"Red": 2, "Blue": 1}, "total": 3, "leaders": ("Red",)}
        tie = {"counts": {"Red": 1, "Blue": 1}, "total": 2, "leaders": ("Red", "Blue")}
        empty = {"counts": {"Red": 0, "Blue": 0}, "total": 0, "leaders": ()}
        self.assertEqual(poll.result_text(single, "complete"), "The winner was Red.")
        self.assertEqual(poll.result_text(single, "aborted"), "The current lead was Red.")
        self.assertEqual(poll.result_text(tie, "complete"), "Tie between Red and Blue.")
        self.assertEqual(poll.result_text(empty, "complete"), "No votes were cast.")
        self.assertEqual(
            poll.result_text({"leaders": ("A", "B", "C"), "counts": {}, "total": 0}, "complete"),
            "Tie between A, B, and C.",
        )


class LivestreamSelectionTests(unittest.TestCase):
    def test_blank_id_uses_the_newest_stream(self):
        older = FakeStream("aa", 10, created=1)
        newer = FakeStream("bb", 11, created=5)
        api = FakeAPI([older, newer])
        self.assertIs(poll.select_livestream(api, ""), newer)
        self.assertIs(poll.select_livestream(api, None), newer)

    def test_id_matches_base_36_or_base_10(self):
        older = FakeStream("aa", 370, created=1)
        newer = FakeStream("bb", 11, created=5)
        api = FakeAPI([older, newer])
        self.assertIs(poll.select_livestream(api, "aa"), older)
        self.assertIs(poll.select_livestream(api, "370"), older)
        self.assertIsNone(poll.select_livestream(api, "nope"))


class OptionValidationTests(unittest.TestCase):
    def test_blank_and_duplicate(self):
        self.assertEqual(poll.option_problem(["A", ""])[0], "Blank option")
        self.assertEqual(poll.option_problem(["A", "A"])[0], "Duplicate options")
        self.assertIsNone(poll.option_problem(["A", "B"]))


class SseStopTests(unittest.TestCase):
    def test_closing_the_stream_unblocks_the_reader(self):
        server = HangServer(("127.0.0.1", 0), HangSSEHandler)
        port = server.server_address[1]
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        session = requests.Session()
        session.trust_env = False
        client = EventSource(f"http://127.0.0.1:{port}/stream", session=session, timeout=5)
        stop = threading.Event()
        errors: list[BaseException] = []

        def reader():
            try:
                next(client)
            except BaseException as exc:
                errors.append(exc)

        try:
            client.connect()
            poll.arm_sse_stop(client, stop)
            worker = threading.Thread(target=reader, daemon=True)
            worker.start()
            time.sleep(0.2)
            started = time.perf_counter()
            poll.interrupt_sse(client, stop)
            self.assertLess(time.perf_counter() - started, 1.0)
            worker.join(timeout=1)
            self.assertFalse(worker.is_alive(), errors)
            self.assertTrue(errors)
        finally:
            try:
                client.close()
            except Exception:
                pass
            session.close()
            server.shutdown()
            server.server_close()


class WindowTests(unittest.TestCase):
    def test_minimum_options_and_snapshot_display(self):
        window = poll.PollWindow(load_api=False)
        window.withdraw()
        try:
            self.assertEqual(len(window.option_wgs), 2)
            self.assertEqual(str(window.option_wgs[0].delete_button["state"]), tk.DISABLED)
            window.add_option()
            self.assertEqual(str(window.option_wgs[0].delete_button["state"]), tk.NORMAL)
            window.delete_option(window.option_wgs[-1])
            self.assertEqual(len(window.option_wgs), 2)
            self.assertEqual(str(window.option_wgs[0].delete_button["state"]), tk.DISABLED)

            window.option_wgs[0].option_name.set("Red")
            window.option_wgs[1].option_name.set("Blue")
            window._enter_running(time.monotonic() + 30)
            window._apply_snapshot({"counts": {"Red": 1, "Blue": 1}, "total": 2, "leaders": ("Red", "Blue")})
            window._refresh_status()
            self.assertIn("Votes: 2", window.status.get())
            self.assertEqual(window.option_wgs[0].option_label_value.get(), "Red: 50%")
            self.assertEqual(window.option_wgs[1].option_label_value.get(), "Blue: 50%")
            window._apply_snapshot({"counts": {"Red": 0, "Blue": 0}, "total": 0, "leaders": ()})
            window._refresh_status()
            self.assertEqual(window.option_wgs[0].option_label_value.get(), "Red: 0%")
            self.assertIn("Votes: 0", window.status.get())
        finally:
            window.destroy()


class FakeStream:
    def __init__(self, stream_id: str, stream_id_b10: int, created: float):
        self.stream_id = stream_id
        self.stream_id_b10 = stream_id_b10
        self.created_on = created


class FakeAPI:
    def __init__(self, streams: list[FakeStream]):
        self.livestreams = {stream.stream_id: stream for stream in streams}

    @property
    def latest_livestream(self):
        if not self.livestreams:
            return None
        return max(self.livestreams.values(), key=lambda stream: stream.created_on)


class HangServer(ThreadingHTTPServer):
    daemon_threads = True


class HangSSEHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        try:
            self.wfile.write(b": ping\n\n")
            self.wfile.flush()
            time.sleep(30)
        except Exception:
            return

    def log_message(self, format, *args):
        return


if __name__ == "__main__":
    unittest.main()
