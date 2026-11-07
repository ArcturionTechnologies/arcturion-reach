#!/usr/bin/env python3
"""Live Reach — shared core.

The Founder drops a message into a running agent's mailbox; a PostToolUse hook
(and a Stop guard) inside that agent's session drains it between tool calls and
injects it into the live context. This module is the single source of truth for:

  • where mailboxes live and how cooperating writers and drainers coordinate
  • how injected messages are framed (excuse-me = soft, interrupt = hard)
  • how a human-friendly AGENT name resolves to the live session(s) to hit

Keep it dependency-free (stdlib only) — it runs inside every agent's hook path.
"""
from __future__ import annotations

import datetime as _dt
import fcntl
from contextlib import contextmanager
import json
import os
import re

# Mailbox keyed by Claude session id (same id the hook receives on stdin and the
# id stored as `sessionId` in ~/.claude/sessions/<pid>.json).
# Dirs are env-overridable so tests can isolate them; prod uses the defaults.
MAILBOX_DIR = os.environ.get("REACH_MAILBOX_DIR") or os.path.expanduser(
    "~/.config/arcturion/reach/mailbox")
SESSIONS_DIR = os.environ.get("REACH_SESSIONS_DIR") or os.path.expanduser(
    "~/.claude/sessions")
PROJECTS_DIR = os.environ.get("REACH_PROJECTS_DIR") or os.path.expanduser(
    "~/.claude/projects")

VALID_MODES = ("excuse-me", "interrupt")


# ---------------------------------------------------------------------------
# Mailbox
# ---------------------------------------------------------------------------
def mailbox_path(session_id: str) -> str:
    # session ids are UUID-shaped; guard against path traversal regardless.
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", session_id or "unknown")
    return os.path.join(MAILBOX_DIR, safe + ".jsonl")


def has_pending(session_id: str) -> bool:
    """Fast, advisory check; a later writer may enqueue after this returns."""
    try:
        return os.path.getsize(mailbox_path(session_id)) > 0
    except FileNotFoundError:
        return False


@contextmanager
def _mailbox_lock(path: str, *, blocking: bool = True):
    """Lock a stable per-mailbox sidecar; all cooperating processes use it."""
    fd = os.open(path + ".lock", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        flags = fcntl.LOCK_EX if blocking else fcntl.LOCK_EX | fcntl.LOCK_NB
        fcntl.flock(fd, flags)
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def enqueue(session_id: str, mode: str, body: str) -> str:
    """Append a complete message while holding the mailbox lock."""
    if mode not in VALID_MODES:
        raise ValueError("mode must be one of %r" % (VALID_MODES,))
    if not isinstance(body, str):
        raise ValueError("message body must be a string")
    rec = {
        "mode": mode,
        "body": body,
        "ts": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "from": "founder",
    }
    line = (json.dumps(rec, ensure_ascii=False) + "\n").encode("utf-8")
    os.makedirs(MAILBOX_DIR, exist_ok=True)
    path = mailbox_path(session_id)
    with _mailbox_lock(path):
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            offset = 0
            while offset < len(line):
                written = os.write(fd, line[offset:])
                if written <= 0:
                    raise OSError("mailbox write made no progress")
                offset += written
        finally:
            os.close(fd)
    return path


def drain(session_id: str, *, blocking: bool = True) -> list[dict]:
    """Return valid queued messages, clearing only after the entire file parses.

    A malformed or incomplete mailbox raises ValueError and remains unchanged.
    With blocking=False, a busy lock raises BlockingIOError without consuming mail.
    This is pickup, not an acknowledgement that a host consumed the result.
    """
    path = mailbox_path(session_id)
    try:
        os.stat(MAILBOX_DIR)
    except FileNotFoundError:
        return []
    with _mailbox_lock(path, blocking=blocking):
        try:
            fh = open(path, "r+b")
        except FileNotFoundError:
            return []
        with fh:
            raw = fh.read()
            if not raw:
                return []
            if not raw.endswith(b"\n"):
                raise ValueError("mailbox has an incomplete final record")
            messages = []
            for number, line in enumerate(raw.split(b"\n")[:-1], 1):
                if not line.strip():
                    continue
                try:
                    rec = json.loads(line.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise ValueError(f"malformed mailbox record at line {number}") from exc
                if (not isinstance(rec, dict) or rec.get("mode") not in VALID_MODES
                        or not isinstance(rec.get("body"), str)):
                    raise ValueError(f"invalid mailbox record at line {number}")
                messages.append(rec)
            os.ftruncate(fh.fileno(), 0)
            return messages


# ---------------------------------------------------------------------------
# Framing
# ---------------------------------------------------------------------------
_EXCUSE_TMPL = (
    "\U0001F4E8 FOUNDER (live, mid-turn): {body}\n"
    "Fold this into what you're doing when it fits naturally; you do not have to "
    "abandon your current step. Briefly acknowledge you saw it in your next reply."
)
_INTERRUPT_TMPL = (
    "⛔ FOUNDER INTERRUPT (mid-turn): STOP your current plan now. Address "
    "this before any further tool calls, and confirm the new direction back to "
    "the Founder before proceeding:\n{body}"
)


def frame(messages: list[dict]) -> str:
    """Render drained messages into injected context. Interrupts lead."""
    interrupts = [m for m in messages if m.get("mode") == "interrupt"]
    excuses = [m for m in messages if m.get("mode") != "interrupt"]
    parts: list[str] = []
    for m in interrupts:
        parts.append(_INTERRUPT_TMPL.format(body=(m.get("body") or "").strip()))
    for m in excuses:
        parts.append(_EXCUSE_TMPL.format(body=(m.get("body") or "").strip()))
    return "\n\n".join(parts)


def has_interrupt(messages: list[dict]) -> bool:
    return any(m.get("mode") == "interrupt" for m in messages)


# ---------------------------------------------------------------------------
# Session resolution
# ---------------------------------------------------------------------------
def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, owned by someone else — still "alive"
    except OSError:
        return False


def agent_from_cwd(cwd: str) -> str | None:
    """Find a workspace below the configured root; retain legacy Desktop fallback."""
    if not cwd:
        return None
    configured = os.environ.get("REACH_DESKTOP_DIR")
    if configured:
        root = os.path.realpath(os.path.expanduser(configured))
        target = os.path.realpath(cwd)
        try:
            if os.path.commonpath((root, target)) != root:
                return None
            relative = os.path.relpath(target, root)
        except ValueError:
            return None
        if relative == ".":
            return None
        return relative.split(os.sep, 1)[0]
    match = re.search(r"/Desktop/([^/]+)", cwd)
    return match.group(1) if match else None


def transcript_path(cwd: str, session_id: str) -> str | None:
    """Claude stores a session's transcript at projects/<cwd-with-/-as-->/<id>.jsonl."""
    if not cwd or not session_id:
        return None
    slug = cwd.replace("/", "-")
    return os.path.join(PROJECTS_DIR, slug, session_id + ".jsonl")


def last_active(session: dict) -> float:
    """Epoch seconds of a session's last activity.

    Transcript mtime is the true 'is it working right now' signal — it's touched
    on every tool call. Falls back to startedAt (ms) if no transcript yet.
    """
    tp = session.get("transcript")
    if tp:
        try:
            return os.path.getmtime(tp)
        except OSError:
            pass
    started = session.get("startedAt")
    return (started / 1000.0) if isinstance(started, (int, float)) else 0.0


def live_sessions() -> list[dict]:
    """Every currently-running Claude session (from ~/.claude/sessions/*.json).

    Each entry is annotated with `agent`, `transcript`, and `last_active`.
    """
    out: list[dict] = []
    if not os.path.isdir(SESSIONS_DIR):
        return out
    for fn in os.listdir(SESSIONS_DIR):
        if not fn.endswith(".json"):
            continue
        try:
            with open(os.path.join(SESSIONS_DIR, fn), "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError):
            continue
        pid = data.get("pid")
        if not isinstance(pid, int):
            try:
                pid = int(os.path.splitext(fn)[0])
            except ValueError:
                continue
        if not _pid_alive(pid):
            continue
        data.setdefault("pid", pid)
        data["agent"] = agent_from_cwd(data.get("cwd", ""))
        data["transcript"] = transcript_path(data.get("cwd", ""), data.get("sessionId", ""))
        data["last_active"] = last_active(data)
        out.append(data)
    return out


def resolve(agent: str) -> list[dict]:
    """Live sessions for AGENT, most-recently-ACTIVE first (the one working now)."""
    want = (agent or "").strip().casefold()
    matches = [s for s in live_sessions()
               if (s.get("agent") or "").casefold() == want and s.get("sessionId")]
    matches.sort(key=lambda s: s.get("last_active", 0), reverse=True)
    return matches
