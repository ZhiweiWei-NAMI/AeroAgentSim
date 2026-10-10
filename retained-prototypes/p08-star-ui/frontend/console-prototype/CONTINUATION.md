# Required continuation and acceptance

This checkpoint is intended for integration into AeroAgentSim without overwriting existing work. Preserve the repository's current frontend and any independent adapter work. The standalone console resides at `frontend/console-prototype/` in the delivery branch.

## Required work

1. Inspect the checkout's current instructions and relevant frontend conventions. Reconcile the existing integration contracts instead of creating a competing simulation controller.
2. Add a complete Chinese / English language switch with a proper translation dictionary. Include navigation, forms, dialogs, validation messages, status/empty states, source annotations, dynamic content and accessible labels. Persist the preference; switching must not reset the draft, run, selection or view cursor. Keep technical IDs, units, source quotes and user-entered values unchanged.
3. Integrate the authoring surface into the intended frontend entry point or document an explicit approved standalone route. Keep BENCH as future physical and renderer authority. Never call native local-run lifecycle routes for external BENCH runs.
4. Run the included checks/tests/build and relevant repository tests. Add bilingual interaction coverage and maintain existing safety tests.
5. In an allowed browser preview, inspect and capture screenshots at desktop (at least 1440×900) and mobile (at least 390×844), in both Chinese and English. Include scene configuration, entity editor, network research profile and diff, versions, and replay/evidence. Capture actual interface pixels, not recreated mockups.
6. Exercise save/load/import/export, invalid inputs, profile cancel/apply and variants, modal Close/Escape, repeated prepare clicks, navigation during playback, evidence-gap seek and language switching mid-workflow. Check overflow, clipping, contrast, focus, errors and console/network failures.
7. Submit screenshot files plus a concise visual/interaction report for review. Identify the exact tested commit, viewport, language and scenario for each image. A report saying “looks good” without images is insufficient.
8. Keep the PR as a draft. Final acceptance requires explicit visual review approval after any requested corrections. Do not merge or delete the local checkpoint as part of this handoff.

## Boundaries

Do not copy private BENCH/Atlas source, private maps/assets, raw traces or credentials into this public repository. The fixture and source-backed design summaries are independently authored. The Atlas runtime remains absent pending its own distribution authorization. Do not imply that an adapter interface means real live integration has passed.

No paid models, remote simulations, training jobs, access grants or new credentials are required for this continuation. Resolve any genuine missing authorization with the coordinating task before that action.
