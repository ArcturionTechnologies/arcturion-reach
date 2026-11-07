# Source provenance

This repository adapts an internal Arcturion mailbox tool, including a separately reviewed repair to its cooperating file writers and hooks. It begins with a source snapshot; it does not reproduce the original development history. The input hashes below identify that snapshot. The project license covers original Arcturion material; third-party runtime and build tools retain the terms recorded in THIRD_PARTY_NOTICES.md.

SHA-256 of the accepted repair inputs:

| File | SHA-256 |
| --- | --- |
| README.md | d94fcff73214c19fcbc40793e1c91ea0218448307b3eb1fa2e0c5ecceea23cc2 |
| reach_common.py | e0b0f196d6fb7213ed935c215b01f8b35bad64be7f4c35c4bd08ae6dc8bdd87b |
| reach.py | 08c11359c882b3416660840fc07a11c549f665aea13253f99ce67946be2dafdc |
| reach_inject.py | 1d871864076aa58f8d44c6cedf8c334e5918aa4a604211d0efe903e93c9bbf04 |
| reach_stop_guard.py | 3dc11f6b596275fe5b69a73a9d7d24ad87c9bf00140d67383f6065fddd16a536 |
| test_reach.py | 3a521d139434d378b626e3878319e7ee24ec1c3326e511d57d48132f5a730cb4 |
| test_concurrency.py | 0ea397376158083b9e424b97f78080d0c62981f52fed9f59962053068adb9dd7 |

The hook modules and concurrency tests are copied unchanged. The common module adds a configurable workspace root and casefolded session matching, the CLI adds a console-compatible entry point and resolves positional workspace names case-insensitively without unsafe current-session fallback on ambiguous names, and existing tests use synthetic configured workspace roots. The README is rewritten. Packaging, portability tests, the demo, CI, license proposal, and notices are new. Locking, nonblocking hook pickup, and malformed-data handling retain the accepted repair behavior.

Additional routing review: exact session IDs take precedence over prefix matches; ambiguous prefixes fail before enqueue, and a missing workspace root does not block explicit current-session pickup. The self-target tests construct all workspace and session paths rather than relying on their invoking environment. Permission errors and ambiguous workspace names continue to fail without sending.
