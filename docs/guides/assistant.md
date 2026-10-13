# Build a scenario in conversation

The workbench has two ways to edit the same project: the configuration form
and a conversation with the assistant. Both change one scene. The assistant
proposes concrete plans, shows what it assumed, and changes nothing until you
confirm a plan once.

## Connect a model

The assistant uses any OpenAI-compatible chat endpoint. Open the assistant
(**Assistant** in the top bar, or <kbd>A</kbd>) and fill in the endpoint, the
model name and, if the endpoint needs one, an API key. **Connect and test**
saves the connection and sends a short test request; the light next to the
model name shows the result. The key is stored on the server under
`<runs>/assistant/` with owner-only permissions and is never returned to the
browser.

For DeepSeek, use endpoint `https://api.deepseek.com/v1`, a DeepSeek chat model
such as `deepseek-chat`, and your API key. A recorded end-to-end example, from
one Chinese instruction to a finished run, with its transcript and logs, is in
[docs/demos/deepseek-chinese-instruction](../demos/deepseek-chinese-instruction/README.md).

An operator can configure the connection instead:

```sh
AEROAGENTSIM_ASSISTANT_BASE_URL=http://127.0.0.1:8788/v1 \
AEROAGENTSIM_ASSISTANT_MODEL=glm-5.3-flashx \
AEROAGENTSIM_ASSISTANT_API_KEY=... \
aeroagentsim serve --out runs
```

## Describe, review, confirm

Start from the gallery's **Describe a scenario in your own words** card, or
open the assistant inside an existing project. Describe the situation in your
own words, for example:

> A logistics drone notices someone has fallen, hands its parcel to a nearby
> food-delivery courier at a handoff point and photographs the person. The
> courier completes the delivery.

The assistant reads the platform's real capabilities (templates, places,
contracts, algorithms) and drafts a plan. The plan board opens on the left and
the 3D stage previews the proposal as a blueprint. The proposal is not applied
yet, and the stage says so.

The board has three stages, shown in its rail and colour:

| Stage | Meaning |
| --- | --- |
| Proposed | A plan version you are reviewing. Nothing in the project has changed. |
| Configured | The plan was applied as a saved project version. Nothing is running. |
| Running | A run of that version is in progress or finished. |

While a plan is proposed, the board shows:

- **Open questions**: only the ones that change the outcome. Pick an answer,
  or **Use a sensible default**.
- **Assumptions**: everything the assistant filled in for you. **Change**
  starts a message about that assumption.
- **Scene**: the setup layer (environment, participants, places, tasks,
  background events) separate from the behaviour layer (responsibilities,
  rules and decision methods). Each responsibility is either *prescribed*
  (a fixed rule) or *agent decides* (a model decides within the allowed
  options); you can switch it there. Contract chips such as
  `oo:PhysicalAsset › lowalt:UAV` open the AeroGraph contract behind a
  participant.
- **Interactions**: a sequence diagram of who does what. **Play the sequence**
  steps through it and highlights the participants on the stage.
- **Changes**: what changed since the previous version or against the
  starting point, and the typed operations that make up the plan.

The simulator validates every version in the background. **Confirm and
apply** is enabled once the version loads. Confirming saves a new project
version, and the stage materializes the blueprint into the real scene. The
board then lists what was created, changed and removed.

## Revise naturally

Talk to the assistant as you would to a colleague. Each revision is a new plan
version; the version chips on the board let you look back.

- "Make the incident happen later" changes only that.
- "Keep everything else the same" constrains a revision explicitly.
- "Let the courier decide" switches a responsibility to *agent decides*.
- "Show me two options" compiles alternatives side by side; choose one on the
  board.
- "Go back to the previous version" restores an earlier arrangement while
  keeping later unrelated changes.

The form and the conversation can be swapped at any time with the
**Form / Conversation** switch; the project, scene and selection stay.

## Run and read the story

After a plan is configured, **What will I see?** asks the assistant what to
watch for, and **Initialize** prepares the world at t = 0. Start the run from
the top bar as usual.

The **Story** tab compares the plan's expectations with the recorded events:

- *happened*: recorded, with the time and the event; the rewind button seeks
  the run to that moment;
- *pending*: not recorded yet;
- *missed*: the run ended without it, with the recorded reason (for example
  the closest watcher approach, a refused handoff or a task failure);
  **Ask why** asks the assistant about it;
- *branch not taken*: a conditional step whose alternative happened.

Counted expectations, such as "All 6 meal orders are completed", show their
progress. Select an object in the scene and ask about it; the question is sent
with that object as context.

## Evaluate and test

`tools/assistant/eval.py` drives scripted conversations against a running
server and reports turns, model calls, tool errors, compile failures and
latency:

```sh
NO_PROXY=127.0.0.1 python tools/assistant/eval.py \
  --api http://127.0.0.1:8000 --out runs/assistant-eval \
  --conversations flagship vague chinese
```

`tools/assistant/fake_model.py` is a deterministic OpenAI-compatible model used
by `frontend/e2e/assistant.spec.ts` and for demos without a model.

## HTTP API

All routes are under `/v1/assistant`:

| Route | Purpose |
| --- | --- |
| `GET`, `POST /connection`; `POST /connection/test` | Read, set and test the model connection |
| `POST /sessions`; `GET /sessions`; `GET /sessions/{id}` | Create, list and read conversations |
| `POST /sessions/{id}/messages`; `GET /sessions/{id}/events?after=&wait=` | Send a message; long-poll events |
| `POST /sessions/{id}/cancel` | Stop the running turn |
| `GET /sessions/{id}/plans/{version}`; `GET …/plans/{version}/scenario` | Read a plan version and its compiled scenario |
| `POST /sessions/{id}/plans/{version}/choose` | Choose one of several alternatives |
| `POST /sessions/{id}/apply` | Apply a validated version as a project version |
| `GET /sessions/{id}/story/{run_id}` | Expectations against a run's recorded events |
