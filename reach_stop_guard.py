#!/usr/bin/env python3
"""Live Reach — Stop guard.

Intended for a host Stop hook. If mail is pending, it emits a request to block
the stop and includes the drained message as a reason to continue. Whether the
host honors that request is not confirmed by this script.

A successful drain clears the current mailbox; a later Stop sees any newly queued
messages. Host consumption is not acknowledged by this script.

Fail-open: any error just lets the stop proceed normally.
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

    if not rc.has_pending(session_id):
        return

    try:
        messages = rc.drain(session_id, blocking=False)
    except BlockingIOError:
        return  # A busy writer keeps the queued bytes for a later hook.
    if not messages:
        return

    reason = (
        "The Founder sent you a message just as you were finishing. Do not end the "
        "turn yet — handle this first:\n\n" + rc.frame(messages)
    )
    print(json.dumps({"decision": "block", "reason": reason}))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
