# Actual browser verification still required

Status: NOT RUN. No screenshot has been generated, no pixel/layout claim is made, and the blocked browser route has not been bypassed. DOM/model/geometry/HTTP tests are separate evidence.

Use an authorized actual browser to open the local loopback service after `cd ui && npm start`. Record the exact URL, browser version, viewport, graph digest and screenshot filename. Do not treat Canvas calls, jsdom, HTML generation or DOM visibility as visual evidence.

Required captures and interactions:

1. Desktop 1440×1000 and wide 1920×1080: `/ui/`. Confirm first authored `ag.a01` graph appears with light white/gray/blue/turquoise palette. Capture header, accepted/loaded/canvas counts, partial-view omissions and instance index. Confirm no offscreen clipping of controls.
2. Drag to rotate 3D, Shift-drag to pan, wheel/plus/minus to zoom; reset Fit. Capture the same graph after rotation. Node labels must face the screen and remain readable. Test repeated click and drag release outside the canvas.
3. From the index, select a `rule_definition`, expand definition_ast and applicability_ast; capture primitive literals, ordered args, exact state/parameter refs, source artifact pointer and unexecuted semantic status. Follow actual rule/predicate/event links without assuming missing links exist. Use Previous selection and clear/reselect.
4. Select an edge in the inspector, then a parallel or reverse edge. Capture canonical/raw relation, declared source→target, exact role and raw payload. All variants must remain separately inspectable even when labels are dense.
5. Click 2-hop, switch to Flat audit, then pan. Capture focus and legible labels. Ensure viewport-node counts differ honestly from admitted scene counts when nodes are offscreen. Clear filters/focus and verify full loaded counts remain unchanged.
6. Select exact source workflow step; confirm candidate node count and omissions change, while loaded totals do not. Type and relation filters may hide endpoints but must never synthesize shortcut links.
7. Use the global index to select MI20 (or filter scenario directory to MI20), then return to an agriculture/delivery/city case. Capture scenario switch and exact global index target. Run repeated rapid selections and cancel all-case loading; last valid scope must persist.
8. Capture a runtime record with literal UNKNOWN alongside authored expected TRUE, and a source field with null approval/receipt time. Confirm unknown, null, false and not-applicable do not collapse into one state. City engineering approval and delivery professional acceptance are useful concrete checks.
9. Open Coverage and gaps. Capture 35 source workflows, 226 source steps, 20 machine examples, retained conflicts and not-executed warnings. Check download links are local and accessible. Close and reopen without losing selection.
10. Mobile 390×844 and narrow 320×800: capture the reflowed scenario controls, canvas, index and inspector. Check touch/pointer targets, no horizontal document overflow, readable fixed-size labels, safe tooltip placement, and keyboard navigation through native controls.
11. Export a filtered/limited scene. Confirm the export contains the complete loaded nodes/edges, not only the admitted canvas subset. Verify no simulation, external request, command dispatch or telemetry occurs.

Record failures as failures. Do not crop away errors or call a single desktop capture a responsive pass. Fix identified UI issues and rerun affected interactions before declaring visual acceptance.
