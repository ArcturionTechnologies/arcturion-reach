#!/usr/bin/env python3
"""Regression tests for Live Reach. Stdlib only:  python3 -m unittest test_reach"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import reach_common as rc  # noqa: E402

PY = sys.executable


class MailboxTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._orig = rc.MAILBOX_DIR
        rc.MAILBOX_DIR = os.path.join(self.tmp, "mailbox")

    def tearDown(self):
        rc.MAILBOX_DIR = self._orig

    def test_empty_fast_path(self):
        self.assertFalse(rc.has_pending("s1"))
        self.assertEqual(rc.drain("s1"), [])

    def test_enqueue_then_pending(self):
        rc.enqueue("s1", "excuse-me", "hello")
        self.assertTrue(rc.has_pending("s1"))

    def test_drain_delivers_once(self):
        rc.enqueue("s1", "excuse-me", "one")
        rc.enqueue("s1", "interrupt", "two")
        msgs = rc.drain("s1")
        self.assertEqual([m["body"] for m in msgs], ["one", "two"])
        # Second drain is empty — deliver-once.
        self.assertEqual(rc.drain("s1"), [])
        self.assertFalse(rc.has_pending("s1"))

    def test_mailboxes_are_isolated_per_session(self):
        rc.enqueue("s1", "excuse-me", "for-s1")
        self.assertFalse(rc.has_pending("s2"))
        self.assertEqual(rc.drain("s2"), [])
        self.assertEqual([m["body"] for m in rc.drain("s1")], ["for-s1"])

    def test_bad_mode_rejected(self):
        with self.assertRaises(ValueError):
            rc.enqueue("s1", "shout", "no")

    def test_path_traversal_neutralised(self):
        p = rc.mailbox_path("../../etc/passwd")
        self.assertTrue(os.path.basename(p).startswith(".._.._etc_passwd"))
        self.assertTrue(p.startswith(rc.MAILBOX_DIR))


class FramingTests(unittest.TestCase):
    def test_interrupt_leads_and_is_marked(self):
        msgs = [
            {"mode": "excuse-me", "body": "soft note"},
            {"mode": "interrupt", "body": "STOP NOW"},
        ]
        out = rc.frame(msgs)
        self.assertLess(out.index("STOP NOW"), out.index("soft note"))
        self.assertIn("INTERRUPT", out)
        self.assertIn("FOUNDER", out)
        self.assertTrue(rc.has_interrupt(msgs))

    def test_soft_only(self):
        out = rc.frame([{"mode": "excuse-me", "body": "just this"}])
        self.assertIn("just this", out)
        self.assertNotIn("INTERRUPT", out)


class ResolverTests(unittest.TestCase):
    def test_agent_from_cwd(self):
        with mock.patch.dict(os.environ, {"REACH_DESKTOP_DIR": "/tmp/invented-workspaces"}):
            self.assertEqual(rc.agent_from_cwd("/tmp/invented-workspaces/ALTAIR"), "ALTAIR")
            self.assertEqual(rc.agent_from_cwd("/tmp/invented-workspaces/POLARIS/sub/dir"), "POLARIS")
            self.assertIsNone(rc.agent_from_cwd("/tmp/invented-workspaces-other/ALTAIR"))
            self.assertIsNone(rc.agent_from_cwd(""))

    def test_resolve_matches_live_alive_session(self):
        tmp = tempfile.mkdtemp()
        orig = rc.SESSIONS_DIR
        rc.SESSIONS_DIR = tmp
        try:
            # Alive: current process pid. Dead: an almost-certainly-free pid.
            alive = {"pid": os.getpid(), "sessionId": "aaaa1111-live",
                     "cwd": os.path.join(os.environ.get("REACH_DESKTOP_DIR", "/Users/x/Desktop"), "ALTAIR"), "startedAt": 200}
            older = {"pid": os.getpid(), "sessionId": "bbbb2222-old",
                     "cwd": os.path.join(os.environ.get("REACH_DESKTOP_DIR", "/Users/x/Desktop"), "ALTAIR"), "startedAt": 100}
            dead = {"pid": 999999, "sessionId": "cccc3333-dead",
                    "cwd": os.path.join(os.environ.get("REACH_DESKTOP_DIR", "/Users/x/Desktop"), "ALTAIR"), "startedAt": 300}
            other = {"pid": os.getpid(), "sessionId": "dddd4444-other",
                     "cwd": os.path.join(os.environ.get("REACH_DESKTOP_DIR", "/Users/x/Desktop"), "POLARIS"), "startedAt": 400}
            for f in (alive, older, dead, other):
                with open(os.path.join(tmp, "%s.json" % f["sessionId"]), "w") as fh:
                    json.dump(f, fh)
            got = rc.resolve("altair")  # case-insensitive
            ids = [s["sessionId"] for s in got]
            self.assertEqual(ids, ["aaaa1111-live", "bbbb2222-old"])  # most-recent first, dead & other excluded
        finally:
            rc.SESSIONS_DIR = orig

    def test_ranks_by_transcript_mtime(self):
        """Most-recently-ACTIVE (transcript touched) session ranks first, even if
        it started earlier — this is what picks 'the one working right now'."""
        import time
        sdir = tempfile.mkdtemp()
        pdir = tempfile.mkdtemp()
        o_s, o_p = rc.SESSIONS_DIR, rc.PROJECTS_DIR
        rc.SESSIONS_DIR, rc.PROJECTS_DIR = sdir, pdir
        try:
            cwd = os.path.join(os.environ.get("REACH_DESKTOP_DIR", "/Users/x/Desktop"), "ALTAIR")
            slug = cwd.replace("/", "-")
            os.makedirs(os.path.join(pdir, slug))
            # 'busy' started EARLIER (lower startedAt) but its transcript is fresh.
            busy = {"pid": os.getpid(), "sessionId": "busy", "cwd": cwd, "startedAt": 100}
            idle = {"pid": os.getpid(), "sessionId": "idle", "cwd": cwd, "startedAt": 999}
            for s in (busy, idle):
                with open(os.path.join(sdir, s["sessionId"] + ".json"), "w") as fh:
                    json.dump(s, fh)
                open(os.path.join(pdir, slug, s["sessionId"] + ".jsonl"), "w").close()
            now = time.time()
            os.utime(os.path.join(pdir, slug, "idle.jsonl"), (now - 7200, now - 7200))
            os.utime(os.path.join(pdir, slug, "busy.jsonl"), (now - 2, now - 2))
            got = [s["sessionId"] for s in rc.resolve("ALTAIR")]
            self.assertEqual(got, ["busy", "idle"])  # fresh transcript wins over later start
        finally:
            rc.SESSIONS_DIR, rc.PROJECTS_DIR = o_s, o_p


class AgentTokenTests(unittest.TestCase):
    """The leading token is a target only if it names a real ~/Desktop/<AGENT>."""
    def setUp(self):
        import reach
        self.reach = reach
        self.tmp = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.tmp, "ALTAIR"))
        os.makedirs(os.path.join(self.tmp, "POLARIS"))
        self._orig = reach.DESKTOP_DIR
        reach.DESKTOP_DIR = self.tmp

    def tearDown(self):
        self.reach.DESKTOP_DIR = self._orig

    def test_real_agent_token(self):
        self.assertEqual(self.reach.known_agent("ALTAIR"), "ALTAIR")
        self.assertEqual(self.reach.known_agent("altair"), "ALTAIR")  # case-insensitive
        self.assertEqual(self.reach.known_agent("POLARIS"), "POLARIS")

    def test_non_agent_words_are_not_targets(self):
        for w in ["we", "hello", "make", "the", "i", ""]:
            self.assertIsNone(self.reach.known_agent(w), w)


class SelfTargetTests(unittest.TestCase):
    """No agent named + a firing session id in the env → hit THAT exact session."""
    def test_no_agent_targets_firing_session(self):
        tmp = tempfile.mkdtemp()
        mailbox = os.path.join(tmp, "mailbox")
        env = dict(os.environ)
        env["REACH_MAILBOX_DIR"] = mailbox
        env["CLAUDE_CODE_SESSION_ID"] = "firing-123"
        env["REACH_SESSIONS_DIR"] = os.path.join(tmp, "sessions")
        proc = subprocess.run(
            [PY, os.path.join(HERE, "reach.py"), "excuse-me", "hello", "there"],
            env=env, capture_output=True, timeout=15)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn(b"this window", proc.stdout)
        orig = rc.MAILBOX_DIR
        rc.MAILBOX_DIR = mailbox
        try:
            msgs = rc.drain("firing-123")
        finally:
            rc.MAILBOX_DIR = orig
        self.assertEqual([m["body"] for m in msgs], ["hello there"])

    def test_named_agent_overrides_self_target(self):
        # Even with a firing session, naming a real agent routes to that agent.
        tmp = tempfile.mkdtemp()
        env = dict(os.environ)
        env["REACH_MAILBOX_DIR"] = os.path.join(tmp, "mailbox")
        env["CLAUDE_CODE_SESSION_ID"] = "firing-123"
        # The synthetic desktop and session directory exercise explicit targeting.
        proc = subprocess.run(
            [PY, os.path.join(HERE, "reach.py"), "excuse-me", "POLARIS", "hi"],
            env=env, capture_output=True, timeout=15)
        self.assertNotIn(b"this window", proc.stdout + proc.stderr)


class HookIntegrationTests(unittest.TestCase):
    """Drive the actual hook scripts as subprocesses with a patched mailbox dir."""
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        mailbox = os.path.join(self.tmp, "mailbox")
        self._orig = rc.MAILBOX_DIR
        # In-process enqueue AND the subprocess hooks must share one mailbox dir.
        rc.MAILBOX_DIR = mailbox
        self.env = dict(os.environ)
        self.env["REACH_MAILBOX_DIR"] = mailbox

    def tearDown(self):
        rc.MAILBOX_DIR = self._orig

    def _run(self, script, payload):
        proc = subprocess.run(
            [PY, os.path.join(HERE, script)],
            input=json.dumps(payload).encode(),
            capture_output=True, env=self.env, timeout=15,
        )
        return proc

    def test_inject_empty_is_silent_and_clean(self):
        # No mail for this session → no stdout, exit 0.
        proc = self._run("reach_inject.py", {"session_id": "nomail-xyz"})
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout.strip(), b"")

    def test_inject_delivers_and_frames(self):
        rc.enqueue("hook-sess", "interrupt", "pivot to task B")
        proc = self._run("reach_inject.py", {"session_id": "hook-sess"})
        self.assertEqual(proc.returncode, 0)
        out = json.loads(proc.stdout)
        ctx = out["hookSpecificOutput"]["additionalContext"]
        self.assertIn("pivot to task B", ctx)
        self.assertIn("INTERRUPT", ctx)
        self.assertEqual(out["hookSpecificOutput"]["hookEventName"], "PostToolUse")
        # Deliver-once: mailbox now empty.
        self.assertFalse(rc.has_pending("hook-sess"))

    def test_stop_guard_blocks_when_pending(self):
        rc.enqueue("stop-sess", "excuse-me", "one more thing")
        proc = self._run("reach_stop_guard.py", {"session_id": "stop-sess"})
        out = json.loads(proc.stdout)
        self.assertEqual(out["decision"], "block")
        self.assertIn("one more thing", out["reason"])

    def test_stop_guard_silent_when_empty(self):
        proc = self._run("reach_stop_guard.py", {"session_id": "empty-sess"})
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout.strip(), b"")


if __name__ == "__main__":
    unittest.main(verbosity=2)
