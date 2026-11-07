# Reach

Reach is a small local mailbox for offering a message to an agent session during a turn. A command queues a record; a cooperating hook can pick it up and return context to its host. The example uses ordinary files and the Python standard library on macOS or Linux.

## Try a synthetic pickup

With Python 3.10 or newer:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install .
python examples/synthetic_demo.py
```

The demonstration creates temporary directories, supplies an invented session ID, and passes a synthetic message through the command and pickup hook. It does not install hooks or contact an agent. Installing from source may download build tools; runtime has no third-party dependencies. `reach --help` shows the command options.

## Configure your own environment

Set `REACH_MAILBOX_DIR`, `REACH_SESSIONS_DIR`, and `REACH_PROJECTS_DIR` to directories you control. Set `REACH_DESKTOP_DIR` to the parent of your agent workspace directories; its immediate child name identifies the current agent. Without that variable, the legacy Desktop-based convention remains in effect. The command can use `CLAUDE_CODE_SESSION_ID` to address the current session, or resolve a named agent from your configured local session metadata. Inspect the source and your host's hook contract before integrating: no hook or configuration is installed automatically.

`reach_inject` and `reach_stop_guard` are Python modules that read hook input on standard input. The host must actually invoke them and accept their output. Test any integration on disposable data before using it. A local pickup demonstrates neither host acknowledgement nor delivery to a running model.

For a configured workspace named `Design-Team`, `reach interrupt Design-Team "review draft"` selects that agent even when called from another session. Workspace names are matched without regard to case. `reach excuse-me "hello there"` addresses the current session when its first word is not a workspace name; use `reach excuse-me --to Design-Team "hello there"` to specify a target explicitly. A first word matching a workspace name is interpreted as a target, so choose `--to` when meaning could be unclear. Conflicting case-insensitive workspace names are rejected for named selection.

Options may appear before or among the mode and message words. Use `--` before a message beginning with an option-like token, such as `--all`, to treat it as text.

A session selector accepts an exact session ID or a prefix matching exactly one live session. An ambiguous prefix is rejected before anything is queued, including when combined with `--all`; `--all` without a selector explicitly reaches all live sessions for the agent. An unqualified message can still address its exact current session when the workspace directory is absent.

## Boundaries

Cooperating writers and readers use a stable sidecar file with POSIX advisory locking; all participants must use the same protocol. Do not remove lock files while participants are running. Hook pickup is nonblocking: contention defers pickup, which requires a later host invocation. Malformed queued data is preserved and blocks pickup until repaired. Hook failures can be silent to avoid blocking the host.

This is best-effort local pickup, not an exactly-once or crash-durable delivery service. Files are not authenticated or encrypted, and the lock is not a security boundary. Behavior on network filesystems and integration with a live host have not been established.

## Development and rights

Run `python -m unittest test_reach test_concurrency test_portability` with all four `REACH_*_DIR` variables directed to disposable directories. CI installs dependencies before applying its test isolation; tests use synthetic fixtures.

[MIT](LICENSE) covers the original project material in this repository. See [PROVENANCE.md](PROVENANCE.md) for the source snapshot and [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for separate runtime and build-tool terms.
