"""Synthetic mailbox regressions; no real sessions or host integrations."""
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import reach_common as rc


class MailboxSafetyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.original = rc.MAILBOX_DIR
        self.addCleanup(setattr, rc, 'MAILBOX_DIR', self.original)
        rc.MAILBOX_DIR = str(Path(self.tmp.name) / 'mailbox')

    def test_writer_open_before_drain_retains_late_write(self):
        rc.enqueue('synthetic', 'excuse-me', 'first')
        ready, release, finished = threading.Event(), threading.Event(), threading.Event()
        original_write = os.write
        errors, received = [], []

        def delayed_write(fd, data):
            ready.set()
            if not release.wait(5): raise TimeoutError('writer barrier timed out')
            return original_write(fd, data)

        def writer():
            try: rc.enqueue('synthetic', 'interrupt', 'late')
            except Exception as exc: errors.append(exc)

        def reader():
            try: received.extend(rc.drain('synthetic'))
            except Exception as exc: errors.append(exc)
            finally: finished.set()

        with mock.patch.object(rc.os, 'write', side_effect=delayed_write):
            write_thread = threading.Thread(target=writer)
            read_thread = threading.Thread(target=reader)
            write_thread.start()
            try:
                self.assertTrue(ready.wait(5))
                read_thread.start()
                self.assertFalse(finished.wait(0.15), 'reader must wait for writer lock')
            finally:
                release.set()
                write_thread.join(5)
                if read_thread.ident: read_thread.join(5)
        self.assertFalse(write_thread.is_alive())
        self.assertFalse(read_thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual([x['body'] for x in received], ['first', 'late'])
        self.assertEqual(rc.drain('synthetic'), [])

    def test_hooks_defer_busy_mailbox_and_pick_up_after_unlock(self):
        scripts = ('reach_inject.py', 'reach_stop_guard.py')
        env = dict(os.environ, REACH_MAILBOX_DIR=rc.MAILBOX_DIR)
        for index, script in enumerate(scripts):
            with self.subTest(hook=script):
                session = f'busy-{index}'
                rc.enqueue(session, 'excuse-me', 'queued synthetic message')
                path = Path(rc.mailbox_path(session))
                queued = path.read_bytes()
                payload = json.dumps({'session_id': session}).encode()
                argv = [sys.executable, str(Path(__file__).with_name(script))]
                with rc._mailbox_lock(str(path)):
                    with self.assertRaises(BlockingIOError):
                        rc.drain(session, blocking=False)
                    busy = subprocess.run(argv, input=payload, capture_output=True, env=env, timeout=2)
                    self.assertEqual(busy.returncode, 0)
                    self.assertEqual(busy.stdout, b'')
                    self.assertEqual(path.read_bytes(), queued)
                later = subprocess.run(argv, input=payload, capture_output=True, env=env, timeout=2)
                self.assertEqual(later.returncode, 0)
                self.assertIn('queued synthetic message', later.stdout.decode())
                self.assertFalse(rc.has_pending(session))

    def test_concurrent_process_writers_and_drainer(self):
        child_code = ('import reach_common as r,sys; '
                      '[(r.enqueue("shared", "excuse-me", sys.argv[1]+":"+str(i))) for i in range(80)]')
        env = dict(os.environ, REACH_MAILBOX_DIR=rc.MAILBOX_DIR, PYTHONPATH=str(Path(__file__).resolve().parent))
        workers = [subprocess.Popen([sys.executable, '-c', child_code, str(i)], env=env,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE) for i in range(4)]
        collected = []
        deadline = time.monotonic() + 20
        try:
            while any(p.poll() is None for p in workers):
                self.assertLess(time.monotonic(), deadline, 'writers timed out')
                collected.extend(rc.drain('shared'))
                time.sleep(0.002)
            for p in workers:
                _, err = p.communicate(timeout=5)
                self.assertEqual(p.returncode, 0, err.decode(errors='replace'))
            collected.extend(rc.drain('shared'))
        finally:
            for p in workers:
                if p.poll() is None: p.kill()
                p.communicate()
        expected = {f'{worker}:{i}' for worker in range(4) for i in range(80)}
        bodies = [m['body'] for m in collected]
        self.assertEqual(len(bodies), 320)
        self.assertEqual(set(bodies), expected)

    def test_malformed_or_incomplete_records_remain_unchanged(self):
        valid = json.dumps({'mode': 'excuse-me', 'body': 'valid'}).encode() + b'\n'
        for bad in (b'{bad}\n', b'[]\n', b'{"mode":"interrupt","body":5}\n', b'\xff\n', b'{"mode":'):
            with self.subTest(bad=bad):
                os.makedirs(rc.MAILBOX_DIR, exist_ok=True)
                path = Path(rc.mailbox_path('broken'))
                original = valid + bad
                path.write_bytes(original)
                with self.assertRaisesRegex(ValueError, 'mailbox'):
                    rc.drain('broken')
                self.assertEqual(path.read_bytes(), original)

    def test_truncate_failure_propagates_and_retains_data(self):
        rc.enqueue('synthetic', 'excuse-me', 'kept')
        path = Path(rc.mailbox_path('synthetic'))
        original = path.read_bytes()
        for error in (OSError('synthetic failure'), FileNotFoundError('synthetic failure')):
            with self.subTest(error=type(error).__name__), mock.patch.object(rc.os, 'ftruncate', side_effect=error):
                with self.assertRaisesRegex(OSError, 'synthetic failure'):
                    rc.drain('synthetic')
            self.assertEqual(path.read_bytes(), original)
        self.assertEqual(rc.drain('synthetic')[0]['body'], 'kept')

    def test_failed_partial_write_is_reported_and_left_for_repair(self):
        original_write = os.write
        calls = []
        def failing_write(fd, data):
            calls.append(True)
            if len(calls) == 1: return original_write(fd, data[:4])
            raise OSError('synthetic partial write failure')
        with mock.patch.object(rc.os, 'write', side_effect=failing_write):
            with self.assertRaisesRegex(OSError, 'partial write failure'):
                rc.enqueue('synthetic', 'excuse-me', 'unfinished')
        path = Path(rc.mailbox_path('synthetic'))
        original = path.read_bytes()
        with self.assertRaisesRegex(ValueError, 'incomplete'):
            rc.drain('synthetic')
        self.assertEqual(path.read_bytes(), original)

    def test_partial_writes_are_completed(self):
        original_write = os.write
        def short_write(fd, data):
            return original_write(fd, data[:3])
        with mock.patch.object(rc.os, 'write', side_effect=short_write):
            rc.enqueue('synthetic', 'excuse-me', 'whole message')
        self.assertEqual(rc.drain('synthetic')[0]['body'], 'whole message')

if __name__ == '__main__':
    unittest.main()
