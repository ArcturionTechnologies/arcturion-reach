"""Exercise a local mailbox using only invented temporary paths and data."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

with tempfile.TemporaryDirectory(prefix="reach-demo-") as directory:
    root = Path(directory)
    env = {"PATH": os.defpath, "PYTHONDONTWRITEBYTECODE": "1",
           "CLAUDE_CODE_SESSION_ID": "synthetic-session"}
    for key, name in (("REACH_MAILBOX_DIR", "mailbox"),
                      ("REACH_SESSIONS_DIR", "sessions"),
                      ("REACH_PROJECTS_DIR", "projects"),
                      ("REACH_DESKTOP_DIR", "workspaces")):
        path = root / name
        path.mkdir()
        env[key] = str(path)
    subprocess.run([sys.executable, "-m", "reach", "excuse-me", "A synthetic message"],
                   cwd=root, env=env, check=True, capture_output=True, text=True)
    result = subprocess.run([sys.executable, "-m", "reach_inject"], cwd=root, env=env,
                            input=json.dumps({"session_id": "synthetic-session"}),
                            check=True, capture_output=True, text=True)
    assert "A synthetic message" in result.stdout, result.stdout
    print("Synthetic message queued and picked up locally.")
