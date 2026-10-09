# Console product recordings

These draft GIFs show the real console and backend at 1280 × 800, exported at
960 px and 12 fps. Backend idle waits are cut from marked video segments.
The overview explicitly uses a completed replay for its Inspect view; the
simulation story starts and operates a new run through real accident admission,
award and browser-rendered capture.

| Recording | Story |
| --- | --- |
| [00-overview.gif](00-overview.gif) | Home → Studio → Run → synchronized Inspect |
| [01-configure-plugins.gif](01-configure-plugins.gif) | PX4, kinematic, SUMO, wind and field ownership |
| [02-predicates.gif](02-predicates.gif) | AeroGraph field picker, threshold edit and compiler validation |
| [03-rules-events.gif](03-rules-events.gif) | Predicate rule, type bindings, chains and injection points |
| [04-aerograph.gif](04-aerograph.gif) | Type ancestry, field writers and a workspace entity |
| [05-agents.gif](05-agents.gif) | LangGraph configuration, grants and recorded decisions |
| [06-simulate.gif](06-simulate.gif) | Validate, start, inject accident, award and city capture |
| [07-visualize.gif](07-visualize.gif) | Cameras, event timeline, shared selection and photo |

Reproduce from the repository root with `scripts/record-docs-media.sh`.
See [recording setup and options](../../tools/docs/README.md).
Re-record after the operator-wait and procedural-city changes merge.
Native plugin settings are authored drafts; the live-agent story configures a
future provider without making a paid model call. The completed agent replay
uses the shipped recorded responses through the production LangGraph executor.
