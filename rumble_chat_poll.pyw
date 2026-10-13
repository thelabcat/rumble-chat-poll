#!/usr/bin/env python3
"""Poll a Rumble livestream chat.

Chat votes arrive on Cocorum's SSE chat client. The socket reader runs on a
worker thread and posts plain snapshots to a queue. Tkinter widgets are
updated only on the main thread.

S.D.G.
"""

from __future__ import annotations

import queue
import sys
import threading
import time
import tomllib
from datetime import timedelta
from pathlib import Path

import requests
import tkinter as tk
from cocorum import RumbleAPI, chatapi
from requests_sse import ReadyState
from tkinter import messagebox, simpledialog, ttk

MINIMUM_OPTIONS = 2
STATUS_ROW = 0
OPT_FRAME_ROW = 1
ADD_BTTN_ROW = 2
ABORT_BTTN_ROW = 2
START_BTTN_ROW = 3
UI_POLL_MS = 100
# Short enough that Abort does not sit through requests-sse's usual 5s backoff,
# long enough that a dropped socket does not retry in a tight loop.
SSE_RECONNECT_DELAY = timedelta(milliseconds=200)
# Applies to reconnect attempts. The live read stays blocking so a quiet chat
# is not treated as "stream ended" (Cocorum maps read timeouts to chat closed).
SSE_CONNECT_TIMEOUT = 10

RESULT_TITLES = {
    "complete": "Poll complete",
    "aborted": "Poll aborted",
    "chat_closed": "Chat closed",
    "error": "Poll failed",
}


def bundle_dir() -> Path:
    """Directory of read-only bundled files (source tree, or the PyInstaller extract dir)."""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent


def config_dir() -> Path:
    """Directory of config.toml and the saved API URL."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def resolve_config_path(name: str) -> Path:
    path = Path(name)
    if path.is_absolute():
        return path
    return config_dir() / path


def load_config() -> dict:
    config_path = config_dir() / "config.toml"
    if not config_path.exists():
        template_path = bundle_dir() / "config_template.toml"
        config_path.write_text(template_path.read_text(encoding="utf-8"), encoding="utf-8")
    with config_path.open("rb") as handle:
        return tomllib.load(handle)


CONFIG = load_config()


def join_names(names: tuple[str, ...]) -> str:
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return f"{names[0]} and {names[1]}"
    return ", ".join(names[:-1]) + f", and {names[-1]}"


def result_text(snapshot: dict, reason: str) -> str:
    """Sentence describing the current tally."""
    leaders = snapshot["leaders"]
    if not leaders:
        return "No votes were cast."
    if len(leaders) > 1:
        return f"Tie between {join_names(leaders)}."
    if reason == "complete":
        return f"The winner was {leaders[0]}."
    return f"The current lead was {leaders[0]}."


def option_problem(names: list[str]) -> tuple[str, str] | None:
    """Return a dialog title and message when the option list cannot start a poll."""
    if any(name == "" for name in names):
        return "Blank option", "Options cannot be blank."
    if len(set(names)) != len(names):
        return "Duplicate options", "Options must all be unique."
    return None


def select_livestream(api, livestream_id):
    """Pick a livestream by base-36 id, base-10 id, or the newest one."""
    if not livestream_id:
        return api.latest_livestream
    wanted = str(livestream_id).strip()
    for stream in api.livestreams.values():
        if stream.stream_id == wanted or str(stream.stream_id_b10) == wanted:
            return stream
    return None


class Ballot:
    """One vote per account. A vote is the option text, or its 1-based index."""

    def __init__(self, options: list[str], numeric: bool = True):
        self.options = list(options)
        self.numeric = numeric
        self.counts = {option: 0 for option in self.options}
        self.voters: set[int] = set()

    def choice_for(self, text: str) -> str | None:
        text = text.strip()
        if text in self.counts:
            return text
        if not self.numeric:
            return None
        try:
            number = int(text)
        except ValueError:
            return None
        # Reject "01", "+1", and other spellings int() would otherwise accept.
        if str(number) != text or not 1 <= number <= len(self.options):
            return None
        return self.options[number - 1]

    def cast(self, user_id: int, text: str) -> str | None:
        if user_id in self.voters:
            return None
        choice = self.choice_for(text)
        if choice is None:
            return None
        self.voters.add(user_id)
        self.counts[choice] += 1
        return choice

    def snapshot(self) -> dict:
        counts = dict(self.counts)
        total = sum(counts.values())
        if total == 0:
            leaders: tuple[str, ...] = ()
        else:
            top = max(counts.values())
            leaders = tuple(option for option, count in counts.items() if count == top)
        return {"counts": counts, "total": total, "leaders": leaders}


def arm_sse_stop(client, stop_event: threading.Event) -> None:
    """Let a blocked SSE read end once stop_event is set.

    requests-sse reconnects after the socket closes, and that reconnect can
    block with no timeout. Refuse further connects once the poll is stopping,
    and give reconnect attempts a deadline.
    """
    client._timeout = SSE_CONNECT_TIMEOUT
    client._reconnection_time = SSE_RECONNECT_DELAY
    client._orginal_reconnection_time = SSE_RECONNECT_DELAY
    original_connect = client.connect

    def connect(retry: int = 0):
        if stop_event.is_set():
            client._ready_state = ReadyState.CLOSED
            raise requests.exceptions.ConnectionError("poll stopped")
        return original_connect(retry)

    client.connect = connect


def interrupt_sse(client, stop_event: threading.Event) -> None:
    """Unblock EventSource.__next__ waiting on the chat stream.

    response.close() from this thread can sit until the blocked read gives up.
    Shutting down the socket read wakes that read immediately. The worker
    closes the response after it notices the stop.
    """
    stop_event.set()
    client._reconnection_time = timedelta(0)
    client._orginal_reconnection_time = timedelta(0)
    client._ready_state = ReadyState.CLOSED
    response = getattr(client, "_response", None)
    raw = getattr(response, "raw", None) if response is not None else None
    if raw is not None and hasattr(raw, "shutdown"):
        try:
            raw.shutdown()
            return
        except Exception:
            pass
    if response is not None:
        try:
            response.close()
        except Exception:
            pass


class Poll(threading.Thread):
    """Read chat votes until the timer, Abort, or the chat stream ends."""

    def __init__(
        self,
        api_url: str,
        options: list[str],
        duration: float,
        ui_queue: queue.Queue,
        livestream_id=None,
        numeric: bool = True,
    ):
        super().__init__(daemon=True, name="rumble-chat-poll")
        self.api_url = api_url
        self.ballot = Ballot(options, numeric=numeric)
        self.duration = duration
        self.ui_queue = ui_queue
        self.livestream_id = livestream_id
        self._stop = threading.Event()
        self._state_lock = threading.Lock()
        self._chat_lock = threading.Lock()
        self._stop_reason: str | None = None
        self._listening = False
        self.chat = None
        self.start_wall = 0.0
        self.end_wall = 0.0
        self.deadline = 0.0

    def request_stop(self, reason: str) -> None:
        """Ask the reader to finish, and unblock it if it is waiting on chat."""
        with self._state_lock:
            if self._stop.is_set():
                return
            self._stop_reason = reason
            self._stop.set()
        self._interrupt_chat()

    def _set_reason(self, reason: str) -> None:
        with self._state_lock:
            if not self._stop.is_set():
                self._stop_reason = reason
                self._stop.set()

    def _reason(self) -> str:
        with self._state_lock:
            return self._stop_reason or "complete"

    def _interrupt_chat(self) -> None:
        with self._chat_lock:
            chat = self.chat
        if chat is None:
            return
        try:
            interrupt_sse(chat.client, self._stop)
        except Exception:
            pass

    def _close_chat(self) -> None:
        with self._chat_lock:
            chat = self.chat
            self.chat = None
        if chat is None:
            return
        try:
            chat.close()
        except Exception:
            pass

    def run(self) -> None:
        try:
            self._run()
        except Exception as exc:
            self._fail(exc)
        finally:
            self._close_chat()

    def _fail(self, exc: Exception) -> None:
        message = str(exc) or exc.__class__.__name__
        if self._listening:
            self.ui_queue.put(("finished", self.ballot.snapshot(), "error", message))
        else:
            self.ui_queue.put(("connect_failed", "Could not run the poll", message))

    def _run(self) -> None:
        if self._stop.is_set():
            return
        try:
            api = RumbleAPI(self.api_url, refresh_rate=30)
        except AssertionError as exc:
            raise RuntimeError(f"The livestream API request failed ({exc}).") from exc
        if self._stop.is_set():
            return

        livestream = select_livestream(api, self.livestream_id)
        if livestream is None:
            if self.livestream_id:
                self.ui_queue.put((
                    "connect_failed",
                    "Livestream not found",
                    "No livestream matches the specified ID.",
                ))
            else:
                self.ui_queue.put((
                    "connect_failed",
                    "You are not live",
                    "Could not find any livestreams at the configured API URL.",
                ))
            return

        # stream_id is base 36. ChatAPI treats a str as base 36.
        chat = chatapi.ChatAPI(livestream.stream_id)
        arm_sse_stop(chat.client, self._stop)
        with self._chat_lock:
            self.chat = chat
        if self._stop.is_set():
            return

        chat.clear_mailbox()
        self.start_wall = time.time()
        self.end_wall = self.start_wall + self.duration
        self.deadline = time.monotonic() + self.duration
        self._listening = True
        self.ui_queue.put(("started", self.deadline))
        self._listen()
        self.ui_queue.put(("finished", self.ballot.snapshot(), self._reason(), None))

    def _listen(self) -> None:
        assert self.chat is not None
        while not self._stop.is_set():
            if time.monotonic() >= self.deadline:
                self._set_reason("complete")
                break
            try:
                message = self.chat.get_message()
            except requests.RequestException:
                if self._stop.is_set():
                    break
                raise
            if message is None:
                self._set_reason("chat_closed")
                break
            self._consider(message)

    def _consider(self, message) -> None:
        try:
            if message.deleted or message.raid_notification or message.gift_purchase_notification:
                return
            if message.time < self.start_wall or message.time >= self.end_wall:
                return
            username = message.user.username if message.user is not None else str(message.user_id)
            choice = self.ballot.cast(message.user_id, message.text)
        except Exception as exc:
            print("Skipping unreadable chat message:", exc)
            return
        if choice is None:
            return
        print(f"{username} voted for {choice}")
        self.ui_queue.put(("update", self.ballot.snapshot()))


class OptionWidgetGroup:
    def __init__(self, master, option_name: str = "", enable_delete: bool = True):
        self.master = master
        self.row = 0
        self.option_name = tk.StringVar(self.master.option_frame, option_name)
        self.__enable_delete = enable_delete
        self.configstate_build()

    def place_on_row(self, row: int = 0) -> None:
        self.row = row
        self.option_field.grid(row=row, column=0, sticky=tk.NSEW)
        self.delete_button.grid(row=row, column=1, sticky=tk.NSEW)

    def configstate_destroy(self) -> None:
        self.option_field.destroy()
        self.delete_button.destroy()

    @property
    def enable_delete(self) -> bool:
        return self.__enable_delete

    @enable_delete.setter
    def enable_delete(self, value: bool) -> None:
        self.__enable_delete = value
        self.delete_button["state"] = tk.NORMAL if value else tk.DISABLED

    def configstate_build(self) -> None:
        self.option_field = tk.Entry(self.master.option_frame, textvariable=self.option_name)
        self.delete_button = tk.Button(
            self.master.option_frame,
            text="Delete",
            command=lambda: self.master.delete_option(self),
        )
        self.enable_delete = self.__enable_delete

    def switch_to_viewstate(self) -> None:
        self.configstate_destroy()
        self.option_label_value = tk.StringVar(self.master.option_frame)
        self.option_label = tk.Label(self.master.option_frame, textvariable=self.option_label_value)
        self.option_label.grid(row=self.row, column=0, sticky=tk.EW)
        self.option_amount_pb = ttk.Progressbar(
            self.master.option_frame,
            orient=tk.HORIZONTAL,
            length=100,
            mode="determinate",
        )
        self.option_amount_pb.grid(row=self.row, column=1, sticky=tk.NSEW)
        self.percentage = 0

    @property
    def percentage(self) -> float:
        return self.option_amount_pb["value"]

    @percentage.setter
    def percentage(self, new_percentage: float) -> None:
        shown = int(new_percentage)
        if 0 <= shown <= 100:
            self.option_amount_pb["value"] = shown
            self.option_label_value.set(f"{self.option_name.get()}: {shown}%")


class PollWindow(tk.Tk):
    def __init__(self, *, load_api: bool = True):
        super().__init__()
        self.title("Rumble Chat Poll")
        self.option_wgs: list[OptionWidgetGroup] = []
        self.api_url: str | None = None
        self.poll: Poll | None = None
        self.ui_queue: queue.Queue = queue.Queue()
        self._phase = "config"
        self._closing = False
        self._deadline: float | None = None
        self._snapshot: dict | None = None
        self.abort_button: tk.Button | None = None
        self.configstate_build(firstrun=True)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(UI_POLL_MS, self._pump)
        if load_api:
            self.get_api_url()
            if not self.api_url:
                self.destroy()

    def get_api_url(self, from_file: bool = True) -> None:
        url_path = resolve_config_path(CONFIG.get("apiURLFile", "api_url.txt"))
        if from_file:
            try:
                self.api_url = url_path.read_text(encoding="utf-8").strip() or None
            except FileNotFoundError:
                self.api_url = None
            if not self.api_url:
                self.get_api_url(from_file=False)
            return

        input_url = simpledialog.askstring(
            "Enter Your API URL",
            "Paste your Rumble API URL from https://rumble.com/account/livestream-api",
            parent=self,
        )
        if not input_url and not self.api_url:
            messagebox.showerror(
                "Need API URL",
                "The program needs your Rumble API URL to access chat messages.",
                parent=self,
            )
        elif input_url:
            self.api_url = input_url.strip()
            url_path.write_text(self.api_url + "\n", encoding="utf-8")

    def reset_config(self) -> None:
        config_path = config_dir() / "config.toml"
        if config_path.exists():
            config_path.unlink()
        messagebox.showinfo(
            "Configuration reset",
            "The configuration file was deleted and will be regenerated on the next launch.",
            parent=self,
        )
        self.destroy()

    def configstate_build(self, firstrun: bool = False) -> None:
        if firstrun:
            self.geometry("300x200")
            self.menubar = tk.Menu(self)
            self["menu"] = self.menubar
            self.menunames: list[str] = []

            self.file_menu = tk.Menu(self.menubar)
            self.file_menu.add_command(label="Change API URL", command=lambda: self.get_api_url(from_file=False))
            self.file_menu.add_command(label="Reset config", command=self.reset_config)
            self.menubar.add_cascade(label="File", menu=self.file_menu)
            self.menunames.append("File")

            self.duration_menu = tk.Menu(self.menubar)
            self.duration = tk.IntVar(self)
            self.duration.set(int(CONFIG.get("durationDefault", 60)))
            for dur_option, seconds in CONFIG.get("durationOptions", {"1 minute": 60}).items():
                self.duration_menu.add_radiobutton(label=dur_option, variable=self.duration, value=int(seconds))
            self.menubar.add_cascade(label="Duration", menu=self.duration_menu)
            self.menunames.append("Duration")

            self.option_frame = tk.Frame(self)
            self.option_frame.grid(row=OPT_FRAME_ROW, column=0, sticky=tk.NSEW)
            self.rowconfigure(OPT_FRAME_ROW, weight=1)

            self.status = tk.StringVar(self)
            self.status_display = tk.Label(self, textvariable=self.status)
            self.status_display.grid(row=STATUS_ROW, column=0, sticky=tk.NSEW)

        while len(self.option_wgs) < MINIMUM_OPTIONS:
            self.add_option(build=False)

        for index, option_wg in enumerate(self.option_wgs):
            option_wg.place_on_row(index)
            self.option_frame.rowconfigure(index, weight=1)
        self.option_frame.columnconfigure(0, weight=1)
        self._sync_delete_buttons()

        if firstrun:
            self.add_option_button = tk.Button(self, text="+", command=self.add_option)
            self.add_option_button.grid(row=ADD_BTTN_ROW, sticky=tk.NSEW)
            self.rowconfigure(ADD_BTTN_ROW, weight=1)

            self.start_button = tk.Button(self, text="Start poll", command=self.start_poll)
            self.start_button.grid(row=START_BTTN_ROW, sticky=tk.NSEW)
            self.rowconfigure(START_BTTN_ROW, weight=1)
            self.columnconfigure(0, weight=1)

    def _sync_delete_buttons(self) -> None:
        allow = len(self.option_wgs) > MINIMUM_OPTIONS
        for option_wg in self.option_wgs:
            option_wg.enable_delete = allow

    def _set_config_locked(self, locked: bool) -> None:
        state = tk.DISABLED if locked else tk.NORMAL
        self.start_button["state"] = state
        self.add_option_button["state"] = state
        for name in self.menunames:
            self.menubar.entryconfig(name, state=state)
        for option_wg in self.option_wgs:
            option_wg.option_field["state"] = state
        if locked:
            for option_wg in self.option_wgs:
                option_wg.delete_button["state"] = tk.DISABLED
        else:
            self._sync_delete_buttons()

    def add_option(self, build: bool = True) -> None:
        self.option_wgs.append(OptionWidgetGroup(self))
        if build:
            self.configstate_build()

    def delete_option(self, option_wg: OptionWidgetGroup) -> None:
        if len(self.option_wgs) <= MINIMUM_OPTIONS:
            return
        option_wg.configstate_destroy()
        self.option_wgs.remove(option_wg)
        self.option_frame.rowconfigure(len(self.option_wgs), weight=0)
        self.configstate_build()

    def start_poll(self) -> None:
        if self._phase != "config":
            return
        names = [option_wg.option_name.get().strip() for option_wg in self.option_wgs]
        problem = option_problem(names)
        if problem:
            messagebox.showerror(problem[0], problem[1], parent=self)
            return
        for option_wg, name in zip(self.option_wgs, names):
            option_wg.option_name.set(name)
        if not self.api_url:
            messagebox.showerror(
                "Need API URL",
                "The program needs your Rumble API URL to access chat messages.",
                parent=self,
            )
            return

        self._phase = "connecting"
        self._snapshot = None
        self._deadline = None
        self.status.set("Connecting to chat...")
        self._set_config_locked(True)
        self.poll = Poll(
            self.api_url,
            names,
            duration=self.duration.get(),
            ui_queue=self.ui_queue,
            livestream_id=CONFIG.get("livestreamId") or None,
        )
        self.poll.start()

    def _enter_running(self, deadline: float) -> None:
        self._phase = "running"
        self._deadline = deadline
        for option_wg in self.option_wgs:
            option_wg.switch_to_viewstate()
        self.add_option_button.destroy()
        self.start_button.destroy()
        for row in (ADD_BTTN_ROW, START_BTTN_ROW):
            self.rowconfigure(row, weight=0)
        self.option_frame.columnconfigure(0, weight=0)
        self.option_frame.columnconfigure(1, weight=1)
        self.abort_button = tk.Button(self, text="Abort poll", command=self.abort_poll)
        self.abort_button.grid(row=ABORT_BTTN_ROW, column=0, sticky=tk.NSEW)
        self._refresh_status()

    def abort_poll(self) -> None:
        if self.poll is None or self._phase != "running":
            return
        self.poll.request_stop("aborted")
        if self.abort_button is not None:
            self.abort_button["state"] = tk.DISABLED
            self.abort_button["text"] = "Aborting..."

    def _apply_snapshot(self, snapshot: dict) -> None:
        self._snapshot = snapshot
        total = snapshot["total"]
        for option_wg in self.option_wgs:
            count = snapshot["counts"].get(option_wg.option_name.get(), 0)
            option_wg.percentage = 0 if total == 0 else 100 * count / total

    def _refresh_status(self) -> None:
        votes = 0 if self._snapshot is None else self._snapshot["total"]
        if self._deadline is None:
            self.status.set(f"Votes: {votes}")
            return
        remaining = max(0, int(self._deadline - time.monotonic() + 0.5))
        self.status.set(f"Votes: {votes}\nApprox {remaining} seconds remaining.")

    def _on_connect_failed(self, title: str, message: str) -> None:
        self._phase = "config"
        self.poll = None
        self.status.set("")
        if self.winfo_exists() and self._phase_widgets_alive():
            self._set_config_locked(False)
        if self._closing:
            return
        messagebox.showerror(title, message, parent=self)

    def _phase_widgets_alive(self) -> bool:
        try:
            return self.start_button.winfo_exists()
        except tk.TclError:
            return False

    def _on_finished(self, snapshot: dict, reason: str, error_message: str | None) -> None:
        self._phase = "done"
        self._apply_snapshot(snapshot)
        self._refresh_status()
        if self.abort_button is not None:
            self.abort_button["state"] = tk.DISABLED
            self.abort_button["text"] = "Ended"
        if self._closing:
            return
        if reason == "error":
            messagebox.showerror("Poll failed", error_message or "The poll failed.", parent=self)
            return
        messagebox.showinfo(RESULT_TITLES.get(reason, "Poll complete"), result_text(snapshot, reason), parent=self)

    def _on_close(self) -> None:
        self._closing = True
        if self.poll is not None:
            self.poll.request_stop("aborted")
        self.destroy()

    def _pump(self) -> None:
        try:
            if not self.winfo_exists():
                return
        except tk.TclError:
            return

        try:
            while True:
                event = self.ui_queue.get_nowait()
                kind = event[0]
                if kind == "started" and self._phase == "connecting":
                    self._enter_running(event[1])
                elif kind == "update" and self._phase == "running":
                    self._apply_snapshot(event[1])
                    self._refresh_status()
                elif kind == "connect_failed":
                    self._on_connect_failed(event[1], event[2])
                elif kind == "finished" and self._phase in ("connecting", "running"):
                    if self._phase == "connecting":
                        self._enter_running(time.monotonic())
                    self._on_finished(event[1], event[2], event[3])
        except queue.Empty:
            pass
        except tk.TclError:
            return

        if self._phase == "running" and self.poll is not None and self._deadline is not None:
            if time.monotonic() >= self._deadline:
                self.poll.request_stop("complete")
            else:
                self._refresh_status()

        try:
            self.after(UI_POLL_MS, self._pump)
        except tk.TclError:
            return


def main() -> None:
    window = PollWindow()
    try:
        alive = window.winfo_exists()
    except tk.TclError:
        return
    if alive:
        window.mainloop()


if __name__ == "__main__":
    main()
