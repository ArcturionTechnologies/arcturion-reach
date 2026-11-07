"""Portable workspace routing exercised only with invented local sessions."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import reach_common as rc


class WorkspaceTests(unittest.TestCase):
    def test_explicit_root_does_not_accept_sibling_prefix(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'workspaces'
            with mock.patch.dict(os.environ, {'REACH_DESKTOP_DIR': str(root)}):
                self.assertEqual(rc.agent_from_cwd(str(root / 'Editor' / 'notes')), 'Editor')
                self.assertIsNone(rc.agent_from_cwd(str(root)))
                self.assertIsNone(rc.agent_from_cwd(str(Path(tmp) / 'workspaces-other' / 'Editor')))

    def test_cli_uses_configured_root_with_synthetic_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root, sessions, projects, mailbox = (base / n for n in ('workspaces', 'sessions', 'projects', 'mailbox'))
            agent = root / 'EDITOR'
            agent.mkdir(parents=True)
            sessions.mkdir()
            projects.mkdir()
            (sessions / 'invented.json').write_text(json.dumps({'pid': os.getpid(), 'sessionId': 'invented-123',
                                                               'cwd': str(agent), 'startedAt': 100}))
            env = dict(os.environ, REACH_DESKTOP_DIR=str(root), REACH_SESSIONS_DIR=str(sessions),
                       REACH_PROJECTS_DIR=str(projects), REACH_MAILBOX_DIR=str(mailbox))
            env.pop('CLAUDE_CODE_SESSION_ID', None)
            script = Path(__file__).with_name('reach.py')
            result = subprocess.run([sys.executable, str(script), 'excuse-me', 'synthetic message'],
                                    cwd=agent, env=env, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('queued', result.stdout)
            with mock.patch.object(rc, 'MAILBOX_DIR', str(mailbox)):
                self.assertEqual([m['body'] for m in rc.drain('invented-123')], ['synthetic message'])

if __name__ == '__main__': unittest.main()
