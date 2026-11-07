#!/usr/bin/env python3
"""Live Reach — Founder CLI.

Drop a message into a running agent's turn:

    reach.py excuse-me ALTAIR "make the button blue, not green"
    reach.py interrupt ALTAIR "stop — do the migration first"
    reach.py --list                      # show every live agent session

Usually invoked via the `excuse-me` / `interrupt` wrappers. You name the AGENT,
not a session id — the live session is resolved for you.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import reach_common as rc  # noqa: E402

BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"

DESKTOP_DIR = os.environ.get("REACH_DESKTOP_DIR") or os.path.expanduser("~/Desktop")


def _fmt_ago(ts: float) -> str:
    if not ts:
        return "unknown"
    d = max(0, int(time.time() - ts))
    if d < 60:
        return "%ds ago" % d
    if d < 3600:
        return "%dm ago" % (d // 60)
    if d < 86400:
        return "%dh ago" % (d // 3600)
    return "%dd ago" % (d // 86400)


def _fmt_session(s: dict) -> str:
    sid = s.get("sessionId", "?")
    return "%s%s%s  pid %s  %s%s%s  active %s  %s" % (
        BOLD, (s.get("agent") or "?"), RESET,
        s.get("pid", "?"),
        DIM, sid[:8], RESET,
        _fmt_ago(s.get("last_active", 0)),
        s.get("cwd", ""),
    )


def cmd_list() -> int:
    sessions = rc.live_sessions()
    agents = [s for s in sessions if s.get("agent")]
    if not agents:
        print("No live agent sessions right now.")
        return 0
    print("Live agent sessions:")
    for s in sorted(agents, key=lambda x: x.get("last_active", 0), reverse=True):
        print("  " + _fmt_session(s))
    return 0


def cmd_agents() -> int:
    """One line per unique live agent, most-active first: 'AGENT — active Xs ago'.
    Machine-friendly feed for the hotkey popup."""
    best: dict[str, dict] = {}
    for s in rc.live_sessions():
        a = s.get("agent")
        if not a:
            continue
        if a not in best or s.get("last_active", 0) > best[a].get("last_active", 0):
            best[a] = s
    for a, s in sorted(best.items(), key=lambda kv: kv[1].get("last_active", 0), reverse=True):
        print("%s — active %s" % (a, _fmt_ago(s.get("last_active", 0))))
    return 0


def cmd_sessions() -> int:
    """One line per live SESSION, most-active first, ' · '-delimited:
    'AGENT · pid P · active Xago · <sid8>'. Feed for the session-level popup."""
    sessions = [s for s in rc.live_sessions() if s.get("agent") and s.get("sessionId")]
    sessions.sort(key=lambda s: s.get("last_active", 0), reverse=True)
    for s in sessions:
        print("%s · pid %s · active %s · %s" % (
            s.get("agent"), s.get("pid"),
            _fmt_ago(s.get("last_active", 0)), s.get("sessionId", "")[:8]))
    return 0


def send_to_session(mode: str, session_id: str, body: str) -> int:
    """Deliver to one exact session id (used for 'reach the window I fired from')."""
    body = body.strip()
    if not body:
        print("Nothing to send — message body is empty.", file=sys.stderr)
        return 2
    meta = next((s for s in rc.live_sessions() if s.get("sessionId") == session_id), None)
    rc.enqueue(session_id, mode, body)
    verb = "⛔ interrupt" if mode == "interrupt" else "📨 excuse-me"
    who = (meta.get("agent") if meta else None) or "current session"
    name = (" · %s" % meta.get("name")) if (meta and meta.get("name")) else ""
    print("%s → %s%s%s (this window%s, %s%s%s) — queued for pickup at a later hook." % (
        verb, BOLD, who, RESET, name, DIM, session_id[:8], RESET))
    return 0


def send(mode: str, agent: str, body: str, session: str | None, to_all: bool) -> int:
    body = body.strip()
    if not body:
        print("Nothing to send — message body is empty.", file=sys.stderr)
        return 2

    matches = rc.resolve(agent)
    if not matches:
        print("%s has no live session right now — nothing to reach." % agent.upper())
        print("  (It'll pick up work from its inbox/queue at its next boot.)")
        return 1

    if session:
        matches = [s for s in matches if s.get("sessionId", "").startswith(session)]
        if not matches:
            print("No live %s session matches --session %s." % (agent.upper(), session),
                  file=sys.stderr)
            return 1

    # Default: hit the single most-recently-ACTIVE session (the one working now).
    # --all broadcasts to every live session of the agent.
    targets = matches if to_all else matches[:1]
    others = len(matches) - len(targets)

    verb = "⛔ interrupt" if mode == "interrupt" else "📨 excuse-me"
    for s in targets:
        rc.enqueue(s["sessionId"], mode, body)
        ago = _fmt_ago(s.get("last_active", 0))
        print("%s → %s%s%s (pid %s, %s%s%s, active %s) — queued for pickup at a later hook." % (
            verb, BOLD, s.get("agent"), RESET,
            s.get("pid"), DIM, s["sessionId"][:8], RESET, ago))

    top = targets[0] if targets else None
    if top and (time.time() - top.get("last_active", 0)) > 120 and not to_all:
        print("  ⚠ that session was last active %s — it may be idle; your message "
              "may be picked up at a later hook." % _fmt_ago(top.get("last_active", 0)))
    if others > 0:
        print("  %d other live %s session%s — `reach.py --list` to see them, "
              "--session <id> to target one, --all to hit all." % (
                  others, agent.upper(), "" if others == 1 else "s"))
    return 0


def known_agent(token: str) -> str | None:
    """A leading token is only a target if it names a real agent workspace
    (~/Desktop/<AGENT>). Otherwise it's just the first word of the message —
    so `excuse-me we need to fix that` doesn't try to reach an agent 'we'."""
    if not token:
        return None
    cand = token.strip().upper()
    if re.fullmatch(r"[A-Z][A-Z0-9_]{1,}", cand) and \
            os.path.isdir(os.path.join(DESKTOP_DIR, cand)):
        return cand
    return None


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="reach", description="Reach a working agent mid-turn.")
    p.add_argument("--list", action="store_true", help="list live agent sessions and exit")
    p.add_argument("--agents", action="store_true", help="one line per live agent (for the popup)")
    p.add_argument("--sessions", action="store_true", help="one line per live session (for the popup)")
    p.add_argument("mode", nargs="?", choices=rc.VALID_MODES,
                   help="excuse-me (soft) or interrupt (hard)")
    p.add_argument("rest", nargs="*",
                   help="[AGENT] message… — AGENT is optional; omit it to reach "
                        "the agent whose workspace you're in")
    p.add_argument("--to", help="target agent explicitly (overrides the first word)")
    p.add_argument("--session", help="disambiguate by session-id prefix")
    p.add_argument("--all", action="store_true", help="send to all live sessions of the agent")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(sys.argv[1:] if argv is None else argv)
    if args.list:
        return cmd_list()
    if args.agents:
        return cmd_agents()
    if args.sessions:
        return cmd_sessions()
    if not args.mode:
        build_parser().print_help(sys.stderr)
        return 2

    tokens = list(args.rest)
    if args.to:
        agent, body = args.to, " ".join(tokens)
    else:
        lead = known_agent(tokens[0]) if tokens else None
        if lead:
            # Explicitly named another agent → its busiest live session.
            agent, body = lead, " ".join(tokens[1:])
        else:
            # No agent named. Safest default: reach the EXACT session this was
            # fired from, so you can never hit the wrong window. Falls back to the
            # workspace's agent only when there's no firing session (plain shell).
            here = os.environ.get("CLAUDE_CODE_SESSION_ID")
            if here and not args.session and not args.all:
                return send_to_session(args.mode, here, " ".join(tokens))
            agent, body = rc.agent_from_cwd(os.getcwd()), " ".join(tokens)

    if not agent:
        print("Couldn't tell which agent to reach. Name one explicitly, e.g. "
              "`excuse-me ALTAIR <message>`.", file=sys.stderr)
        return 2
    return send(args.mode, agent, body, args.session, args.all)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
