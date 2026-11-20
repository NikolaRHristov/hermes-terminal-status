"""Report a Hermes session's live state to its terminal with OSC 7501.

A terminal that understands OSC 7501 (``ESC ] 7501 ; state=<state> BEL``) shows the program in a
pane as an agent with that state. herdr-core, the engine under the Herdr desktop and phone apps, is
the first consumer: it lists a pane as an agent only when the program in it reports, so a Hermes
session with no reporter is a plain shell there however visible its pane is.

The process that sees Hermes' lifecycle events is not the process that owns the terminal. Under the
gateway, every turn, tool call, approval and question runs in the gateway, which has no controlling
terminal, and hook callbacks fire there; the terminal belongs to the CLI's viewer or, under the TUI,
to the launcher. A plugin therefore cannot write to ``/dev/tty``. It works out which device is
showing the session from facts the machine already publishes, and writes one short sequence to that
device - a write that needs no controlling terminal.

Two facts locate a session's device:

* TUI sessions - the launcher hands ``HERMES_TUI_ACTIVE_SESSION_FILE`` to the TUI process it starts,
  and that process inherits the pane variable of the terminal engine it runs in. Both are visible in
  the live process table, so the mapping is exact.
* Classic CLI sessions - the CLI records ``<HERMES_HOME>/terminal-sessions/tty-<device>`` naming the
  session on that terminal. The file name inverts to a device path, and the process table says
  whether that path belongs to a pane this plugin may report to.

Nothing here patches Hermes: documented plugin hooks, ``hermes_constants``, and files Hermes already
writes. Every failure is swallowed, no hook blocks a turn, and a session with no reportable terminal
is left alone.
"""
from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import time
from pathlib import Path
from typing import Optional

STATES = ("idle", "working", "done", "blocked", "error")

#: Name the terminal shows beside the agent.
APP = os.environ.get("HERMES_TERMINAL_STATUS_APP", "hermes")

#: Environment variables a terminal engine sets in the programs it runs. One of them present means
#: the pane consumes status reports; its value names the pane.
PANE_VARIABLES = tuple(
    part.strip()
    for part in os.environ.get("HERMES_TERMINAL_STATUS_PANES", "HC_TERMINAL_ID").split(",")
    if part.strip()
)

#: Pane variables that mean "leave this pane alone": an engine that detects agents by itself needs no
#: reports, and two reporters would disagree with each other.
IGNORED_PANE_VARIABLES = tuple(
    part.strip()
    for part in os.environ.get("HERMES_TERMINAL_STATUS_IGNORE_PANES", "HERDR_PANE_ID").split(",")
    if part.strip()
)

#: ``panes`` reports only inside a pane named above; ``any`` reports in every terminal, which is
#: correct only for terminals that ignore escape sequences they do not understand.
TERMINALS = os.environ.get("HERMES_TERMINAL_STATUS_TERMINALS", "panes").strip().lower()

#: Optional JSON-lines trace of every report, for working out why a terminal shows nothing.
LOG = os.environ.get("HERMES_TERMINAL_STATUS_LOG", "")

#: Seconds to reuse one process-table read across a turn's hooks.
SNAPSHOT_SECONDS = float(os.environ.get("HERMES_TERMINAL_STATUS_TTL", "5") or "5")

#: Hermes' own contract: the launcher hands this to the TUI process it starts.
ACTIVE_SESSION_VARIABLE = "HERMES_TUI_ACTIVE_SESSION_FILE"

_table: tuple[dict, float] = ({"by_session": {}, "devices": {}}, 0.0)


def _homes() -> list[Path]:
    """Every Hermes home whose terminal records may name the session (any profile may be in use)."""
    homes: list[Path] = []
    try:
        from hermes_constants import get_default_hermes_root, get_hermes_home

        homes.append(Path(get_hermes_home()))
        root = Path(get_default_hermes_root())
    except Exception:
        root = Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")
    homes.extend([root, *sorted((root / "profiles").glob("*"))])
    unique: list[Path] = []
    for home in homes:
        if home not in unique:
            unique.append(home)
    return unique


def _is_character_device(path: object) -> bool:
    try:
        return stat.S_ISCHR(os.stat(str(path)).st_mode)
    except (OSError, ValueError, TypeError):
        return False


def _session_of(path: str) -> Optional[str]:
    """The session id recorded in a small JSON record, or None."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    session = data.get("session_id") if isinstance(data, dict) else None
    return session if isinstance(session, str) and session else None


def _device(column: str) -> str:
    """``pts/3`` names a device under /dev, as does an absolute path."""
    return column if column.startswith("/") else f"/dev/{column}"


def _pane(environment: str) -> Optional[str]:
    """The pane this process runs in, when its terminal consumes status reports."""
    if any(re.search(rf"(?:^|\s){re.escape(name)}=", environment) for name in IGNORED_PANE_VARIABLES):
        return None
    for name in PANE_VARIABLES:
        match = re.search(rf"(?:^|\s){re.escape(name)}=(\S+)", environment)
        if match:
            return match.group(1)
    return "" if TERMINALS == "any" else None


def _process_table() -> dict:
    """One read of the process table, as ``{"by_session": {session: device}, "devices": {...}}``.

    ``ps -axEww`` appends each process's environment to its command line, which is the only place a
    running process publishes both the session file it was handed and the pane it inherited."""
    by_session: dict[str, str] = {}
    devices: dict[str, str] = {}
    try:
        done = subprocess.run(
            ["ps", "-axEww", "-o", "pid=,tty=,command="],
            capture_output=True, text=True, timeout=5, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return {"by_session": by_session, "devices": devices}
    for line in done.stdout.splitlines():
        fields = line.split(None, 2)
        if len(fields) < 3 or fields[1] in {"??", "-"}:
            continue
        device = _device(fields[1])
        environment = fields[2]
        pane = _pane(environment)
        if pane is None:
            continue
        devices[device] = pane
        active = re.search(rf"(?:^|\s){ACTIVE_SESSION_VARIABLE}=(\S+)", environment)
        if active:
            session = _session_of(active.group(1))
            if session:
                by_session[session] = device
    return {"by_session": by_session, "devices": devices}


def _snapshot() -> dict:
    global _table
    table, at = _table
    if time.monotonic() - at < SNAPSHOT_SECONDS:
        return table
    table = _process_table()
    _table = (table, time.monotonic())
    return table


def _device_from_record_name(name: str) -> Optional[str]:
    """``tty-dev-pts-3`` -> ``/dev/pts/3``.

    The sanitizer that named the file only replaced slashes, so a device path inverts exactly. Only a
    ``dev-`` prefix is inverted: a multiplexer record (``tty-tmux-pane-3``) names no device and is
    refused rather than turned into a plausible-looking path."""
    stem = name[len("tty-"):] if name.startswith("tty-") else name
    if not stem.startswith("dev-"):
        return None
    return "/" + stem.replace("-", "/")


def _device_for(session: str) -> Optional[str]:
    """The device showing ``session``, when it belongs to a pane this plugin may report to."""
    table = _snapshot()
    known = table["by_session"].get(session)
    if known and _is_character_device(known):
        return known
    for home in _homes():
        try:
            records = sorted((home / "terminal-sessions").glob("tty-*"))
        except OSError:
            continue
        for record in records:
            if _session_of(str(record)) != session:
                continue
            candidate = _device_from_record_name(record.name)
            if candidate and candidate in table["devices"] and _is_character_device(candidate):
                return candidate
    return None


def sequence(state: str) -> bytes:
    """One OSC 7501 report, BEL-terminated."""
    if state not in STATES:
        raise ValueError(f"unknown state: {state}")
    suffix = f":app={APP}" if APP else ""
    return f"\x1b]7501;state={state}{suffix}\x07".encode("ascii")


def _write(device: str, state: str) -> bool:
    """One small non-blocking write to a device. Never raises, never blocks a turn."""
    try:
        handle = os.open(device, os.O_WRONLY | os.O_NOCTTY | os.O_NONBLOCK)
    except OSError:
        return False
    try:
        os.write(handle, sequence(state))
        return True
    except OSError:
        return False
    finally:
        try:
            os.close(handle)
        except OSError:
            pass


def _trace(**record: object) -> None:
    if not LOG:
        return
    try:
        record.update(at=round(time.time(), 2), pid=os.getpid())
        with open(LOG, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n")
    except Exception:
        pass


def _emit(state: str, kwargs: dict) -> None:
    session = kwargs.get("session_id")
    if not isinstance(session, str) or not session:
        return
    try:
        device = _device_for(session)
        written = bool(device) and _write(str(device), state)
        _trace(session=session, state=state, device=device, written=written)
    except Exception:
        return


def _on_approval_request(**kwargs) -> None:
    # An approval decided by an auxiliary model is not a person waiting.
    if kwargs.get("surface") != "smart":
        _emit("blocked", kwargs)


def _on_session_end(**kwargs) -> None:
    if kwargs.get("failed"):
        _emit("error", kwargs)
    elif kwargs.get("interrupted"):
        _emit("idle", kwargs)


HOOKS = {
    "on_session_start": lambda **kw: _emit("idle", kw),
    "on_session_reset": lambda **kw: _emit("idle", kw),
    "pre_llm_call": lambda **kw: _emit("working", kw),
    "post_llm_call": lambda **kw: _emit("done", kw),
    "pre_approval_request": _on_approval_request,
    "post_approval_response": lambda **kw: _emit("working", kw),
    "on_human_input_request": lambda **kw: _emit("blocked", kw),
    "on_human_input_resolved": lambda **kw: _emit("working", kw),
    "on_session_end": _on_session_end,
}


def register(ctx) -> None:
    for name, callback in HOOKS.items():
        try:
            ctx.register_hook(name, callback)
        except Exception:
            continue
