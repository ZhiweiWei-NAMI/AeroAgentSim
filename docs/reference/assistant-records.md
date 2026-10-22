# Assistant records and text provenance

Assistant records distinguish **what the provider said** from **what the platform
shows**. The authoring harness may replace a run summary, correct or filter prose,
and append compiler effects or workflow status. An `assistant` role/event alone
is therefore **not** evidence of model authorship.

The implementation is in [harness.py](../../src/aeroagentsim/assistant/harness.py),
[sessions.py](../../src/aeroagentsim/assistant/sessions.py), and
[api.py](../../src/aeroagentsim/assistant/api.py).

## Storage and HTTP views

Sessions are stored atomically at
`<run_root>/assistant/sessions/<session_id>/session.json`.

| Field | Meaning |
| --- | --- |
| `transcript` | Internal conversation history. Final assistant records keep the compatibility `content`, attributed `parts`, `model_reply`, `status`, and `turn`. Tool-call/result pairs retain their existing shape. Not returned by the session HTTP endpoint. |
| `events` | Ordered UI event log; existing `seq`, `at`, and `kind` remain unchanged. Assistant, notice, and error text has provenance as described below. |
| `turns` | New turn audit records, with turn number, outcome, raw final provider text, emitted text parts, and available statistics. Legacy turns are not reconstructed. |

`GET /v1/assistant/sessions/{id}` includes `events` and `turns`.
`GET /v1/assistant/sessions/{id}/events?after=N&wait=S` returns events after the
cursor plus `last_seq` and `busy`; it does not return the raw completion audit.
Existing plan, application, and connection-summary fields are unchanged.

## Attributed text parts

An assistant, notice, or error event has an ordered `parts` array:

```json
{
  "kind": "assistant",
  "turn": 1,
  "status": "completed",
  "text": "Keep the existing drones.\n\nPlan revision v1 is awaiting validation.",
  "parts": [
    {"source": "model", "text": "Keep the existing drones."},
    {"source": "platform", "text": "Plan revision v1 is awaiting validation."}
  ],
  "suggestions": ["Inspect the proposal"]
}
```

The example omits `seq` and `at`. Provenance values are:

| `source` | Meaning |
| --- | --- |
| `model` | Provider-authored text, without platform prose mixed into that part. Existing display whitespace trimming and suggestion-footer extraction still apply. |
| `platform` | Deterministic summaries, compiler effects, plan/workflow status, corrections, notices, or platform-edited prose. If filtering or correction changes a body, the whole edited body is conservatively attributed to the platform. |
| `unknown` | Missing or unrecognized provenance, including legacy records. Never inferred to be model text from the role, event kind, or model-call count. |

A deterministic summary is platform-authored even if its text happens to equal
the provider's text. Unchanged model prose and appended platform status remain
separate parts. `parts` is authoritative, including an empty array; clients must
not also render the compatibility composite. Assistant `text` joins its parts
with two newlines. `suggestions` retains its existing extracted/filtered string
list; the original footer remains in `model_reply`.

For homogeneous parts, a top-level `source` is a convenience; mixed parts have
no single top-level source. A part missing its own source is `unknown`, even if
the event has a source. Without `parts`, readers use the existing `text` (or
`message` for errors), and a missing source is `unknown`. Historical records are
normalized on read without inventing raw model replies or rewriting their text.

New notices and errors are platform-authored and retain their existing kinds,
`text`/`message`, and error `code`. Text events emitted during a turn carry its
`turn` number; post-turn notices need not. Tool-progress, plan-validation, and
application events retain their existing structured semantics.

## Raw replies and turn outcomes

Each new `turns` entry contains:

| Field | Meaning |
| --- | --- |
| `turn` | Session-local turn number, matching `turn_started`/`turn_finished`. |
| `status` | Typed outcome from the table below. |
| `model_reply` | Exact string content of the final provider message, recorded before suggestion extraction, filtering, grounding, or display composition. Includes original whitespace and suggestion footer. `null` when no final text was received; an empty/whitespace-only string is preserved as such. |
| `parts` | Assistant, notice, and error parts emitted during this turn, in event order. Includes budget/failure notices, not user messages or structured tool/plan events. |
| `elapsed_s` | Optional elapsed time for the whole harness turn, not a per-part generation time. |
| `model_calls` | Optional count of attempted provider calls, including failed calls and the final no-tools request. |
| `tokens` | Optional sum of integer `total_tokens` usage values actually reported by the provider. |

`model_reply` is message **text**, not a copy of the provider response envelope.
It is also retained on the final assistant transcript record when an answer is
published. A rejected empty completion or a display-transformation failure can
therefore have raw text on the turn without an assistant answer event. Intermediate
tool-call narration is not a final reply. A response received after cancellation
is retained as raw final text when applicable, but is not displayed or executed.

| `status` | Meaning |
| --- | --- |
| `running` | Turn accepted; no terminal outcome yet. |
| `completed` | A final completion was received and the display composition published. This does not mean a proposal is valid, applied, or a simulation succeeded. Parts still determine authorship. |
| `budget_exhausted` | Tool budget reached, even if the extra no-tools request returned a usable final message. A platform budget notice is emitted. If the provider continues requesting tools without text, the closing is generated directly as a platform part, never passed off as a model reply. |
| `provider_error` | Provider timeout, transport/HTTP failure, or invalid completion. A safe platform error is emitted, not a successful answer. |
| `error` | Turn startup, configuration, or other harness failure; platform error text. |
| `cancelled` | Cancellation observed; platform notice, no further tool execution. |
| `interrupted` | A running session recovered after a service restart; platform notice and terminal event. |
| `unknown` | Historical or otherwise unavailable outcome. |

`turn_finished.status` repeats the terminal outcome. Assistant answer events and
failure/budget/cancellation notices also carry the applicable status. A provider
failure on the extra budget-limited request ends as `provider_error`, while its
already-emitted budget notice remains in the log. Missing legacy statuses are
not treated as successful outcomes.

## Rendering and boundaries

The conversation panel renders model parts with the existing model avatar and
Markdown. Platform parts use a distinct **Platform note / 平台说明** label;
unknown parts use **Unknown source / 来源未知** (labels follow the UI locale).
Neither platform nor unknown parts receive a model avatar or model-call timing.
Timing is attached only to the last model part of a matching, completed turn.
Raw `model_reply` is audit data, not a replacement display answer.

The final-reply audit copies only message text; turn statistics use an allowlist.
Tool-call history retains its existing message fields. Provider envelopes,
request/response headers, credentials, and connection configuration are not added
to session records. Provider failure
messages are sanitized rather than copying upstream bodies. Conversation text
is retained verbatim for audit; do not submit secrets in conversation content.
Provenance, raw reply, and turn metadata are stripped before history is sent back
to a provider; existing bounded conversation content and tool pairs remain.

Provenance does not authorize actions. Assistant tools still only read or prepare
proposals. Within the assistant API, explicit **Confirm and apply**
(`POST /v1/assistant/sessions/{id}/apply`) remains the sole workspace writer;
recording replies, closing a budget-limited turn, and recovering failures never
apply a proposal or start a simulation.
