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
import reach
from types import SimpleNamespace


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

    def test_named_workspace_with_current_session_never_routes_to_current(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root, sessions, projects, mailbox = (base / n for n in ('workspaces', 'sessions', 'projects', 'mailbox'))
            writer, target = root / 'Writer', root / 'Design-Team'
            writer.mkdir(parents=True)
            target.mkdir()
            sessions.mkdir()
            projects.mkdir()
            for session, cwd in [('writer-session', writer), ('target-session', target)]:
                (sessions / (session + '.json')).write_text(json.dumps({'pid': os.getpid(), 'sessionId': session, 'cwd': str(cwd), 'startedAt': 100}))
            env = dict(os.environ, REACH_DESKTOP_DIR=str(root), REACH_SESSIONS_DIR=str(sessions), REACH_PROJECTS_DIR=str(projects), REACH_MAILBOX_DIR=str(mailbox), CLAUDE_CODE_SESSION_ID='writer-session')
            script = Path(__file__).with_name('reach.py')
            result = subprocess.run([sys.executable, str(script), 'interrupt', 'design-team', 'review draft'], cwd=writer, env=env, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            with mock.patch.object(rc, 'MAILBOX_DIR', str(mailbox)):
                self.assertEqual([m['body'] for m in rc.drain('target-session')], ['review draft'])
                self.assertEqual(rc.drain('writer-session'), [])

    def test_workspace_lookup_rejects_ambiguous_case_and_handles_single_character(self):
        def entry(name):
            return SimpleNamespace(name=name, is_dir=lambda: True)
        listing = mock.MagicMock()
        listing.__enter__.return_value = iter([entry('Design'), entry('design')])
        with mock.patch.object(reach.os, 'scandir', return_value=listing):
            with self.assertRaisesRegex(ValueError, 'Ambiguous'):
                reach.known_agent('DESIGN')
            listing.__enter__.return_value = iter([entry('Design'), entry('design')])
            with mock.patch.object(reach, 'send') as send:
                self.assertEqual(reach.main(['interrupt', '--to', 'DESIGN', 'review']), 2)
                send.assert_not_called()
        listing.__enter__.return_value = iter([entry('X')])
        with mock.patch.object(reach.os, 'scandir', return_value=listing):
            self.assertEqual(reach.known_agent('x'), 'X')
            self.assertIsNone(reach.known_agent('../X'))

    def test_ambiguous_names_fail_without_sending(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root, sessions, projects, mailbox = (base / n for n in ('workspaces', 'sessions', 'projects', 'mailbox'))
            root.mkdir()
            (root / 'Design').mkdir()
            (root / 'design').mkdir(exist_ok=True)
            if len(list(root.iterdir())) != 2:
                self.skipTest('case-insensitive filesystem cannot represent both names')
            sessions.mkdir()
            projects.mkdir()
            env = dict(os.environ, REACH_DESKTOP_DIR=str(root), REACH_SESSIONS_DIR=str(sessions), REACH_PROJECTS_DIR=str(projects), REACH_MAILBOX_DIR=str(mailbox), CLAUDE_CODE_SESSION_ID='writer-session')
            script = Path(__file__).with_name('reach.py')
            result = subprocess.run([sys.executable, str(script), 'interrupt', 'DESIGN', 'review'], cwd=root, env=env, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 2)
            self.assertIn('Ambiguous', result.stderr)
            self.assertFalse(mailbox.exists())

if __name__ == '__main__': unittest.main()
