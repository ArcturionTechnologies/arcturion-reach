#!/usr/bin/env python3
"""Live Reach — PostToolUse injector.

Intended for a host PostToolUse hook. When mail is pending it drains the
mailbox and emits hookSpecificOutput.additionalContext. Host integration,
receipt, and consumption are not confirmed by this script.

Fail-open: the entry point suppresses errors instead of blocking the turn.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import reach_common as rc  # noqa: E402


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return
    session_id = payload.get("session_id")
    if not session_id:
        return

    # Hot path: no mail → do nothing (keeps per-tool-call cost negligible).
    if not rc.has_pending(session_id):
        return

    try:
        messages = rc.drain(session_id, blocking=False)
    except BlockingIOError:
        return  # A busy writer keeps the queued bytes for a later hook.
    if not messages:
        return

    context = rc.frame(messages)
    n = len(messages)
    system_msg = "📨 Picked up %d queued message%s; host consumption is not confirmed." % (
        n, "" if n == 1 else "s")

    print(json.dumps({
        "systemMessage": system_msg,
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": context,
        },
    }))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Absolute backstop — never break the agent's turn.
        pass
