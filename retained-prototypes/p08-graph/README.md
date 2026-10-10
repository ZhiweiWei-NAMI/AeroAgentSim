# P08 graph workbench recovery branch

Start with [RESTORE.md](RESTORE.md) to verify, restore, rebuild, and open the saved graph.

- Workbench source and data: [`p08-graph-workbench/`](p08-graph-workbench/)
- Full stored/original file map and hashes: [`RECOVERY-MANIFEST.json`](p08-graph-workbench/RECOVERY-MANIFEST.json)
- Snapshot verification: [`RECOVERY-VERIFICATION.json`](RECOVERY-VERIFICATION.json)
- Repository file hashes: [`SHA256SUMS`](SHA256SUMS)

This self-contained recovery branch preserves the 2026-10-05 P08 artifact without changing the repository's default branch. Its snapshot-only tree is based on the pinned existing commit 488face80242131638cd69a188678e100bad648e. Current scope is 35 source activities / 226 ordered steps / 265 predicate-definition records. The historical 1,686-entry catalog is not recovered.
