# Decision records

Decision records explain choices about the repository's public contracts,
layout and extension boundary. Records use four-digit ADR-style numbers in
acceptance order, are immutable once accepted, and are superseded by a later
record rather than rewritten; proposed records stay in a PR until accepted.
Each record states status, date, context, decision and consequences. Write one
when a change introduces an extension family, changes the meaning of a shipped
version, moves ownership between this repository and an external project, or
sets a preservation rule that later work must follow.

| Record | Status | Summary |
| --- | --- | --- |
| [0001 — Branch disposition for the rescue drafts](0001-branch-disposition.md) | Accepted, 2026-10-22 | Closed #16 and #18 without merge, kept #17 open for three replacement PRs, preserved every tip as an annotated tag, deleted merged refs, kept `archive/airfogsim`, and recorded the owner-led AeroRescue migration. |
