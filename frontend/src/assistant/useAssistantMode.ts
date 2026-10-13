import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import type { Project } from '../workbench/api';
import { ApiError } from '../workbench/api';
import { AssistantApi, type ApplyRecord, type PlanPreview, type PlanVersion, type Story } from './api';
import type { EditMode, Origin } from './ModeSwitch';
import { rewire } from './motion/rewire';
import { planOverlay, revealOrder, type PreviewOverlay } from './overlay';
import type { Stage } from './PlanBoard';
import { useAssistantSession, useConnection, type AssistantSession } from './useAssistant';
import { draftChanges, proposalWorkspace } from './draftChanges';

const problemText = (problem: unknown) => problem instanceof ApiError ? problem.detail : String(problem);
const PALETTE = { form: '#38c6f4', chat: '#8a7dff' };

/**
 * Everything the workbench needs for the conversational mode: which conversation, which plan version is on
 * screen, what the 3D preview shows, the run story, and the Form ↔ Conversation rewire.
 */
export function useAssistantMode({ apiBase, open, sessionId, projectId, project, locale, runId, terminal, workspaceVersion, update }: {
  apiBase: string; open: boolean; sessionId?: string; projectId?: string; project?: Project; locale: string;
  runId?: string; terminal: boolean; workspaceVersion?: number;
  update: (patch: Record<string, string | undefined>, push?: boolean, path?: string) => void;
}) {
  const api = useMemo(() => new AssistantApi(apiBase), [apiBase]);
  const [morph, setMorph] = useState<{ from: EditMode; to: EditMode; origin: Origin }>();
  // The conversation stays live while its panel rewires away.
  const live = open || morph?.from === 'chat';
  const connection = useConnection(api);
  const assistant = useAssistantSession(api, live ? sessionId : undefined);
  const [error, setError] = useState<string>();

  // Which conversation: the one in the URL, else the project's latest, else a new one.
  const resolving = useRef(false);
  useEffect(() => {
    if (!open || sessionId || resolving.current || (projectId && !project)) return;
    resolving.current = true;
    const abort = new AbortController();
    void (async () => {
      const rows = project ? await api.sessions(project.id, abort.signal) : [];
      const row = rows.filter(item => item.workspace_id === project?.id).sort((a, b) => b.updated_at.localeCompare(a.updated_at))[0];
      const id = row?.id ?? (await api.create(project?.id, locale)).id;
      if (!abort.signal.aborted) update({ assistant: id });
    })().catch(problem => { if (!abort.signal.aborted) setError(problemText(problem)); }).finally(() => { resolving.current = false; });
    return () => { abort.abort(); resolving.current = false; };
  }, [api, open, sessionId, projectId, project?.id]);
  // A conversation about another project is not this project's conversation.
  useEffect(() => {
    const bound = assistant.session?.workspace_id;
    if (open && bound && project && bound !== project.id) update({ assistant: undefined });
  }, [open, assistant.session?.workspace_id, project?.id]);

  // An applied plan remains historical evidence of configuration after Form
  // edits. Field differences below distinguish it from the current draft.
  const plan = assistant.plan, applied = assistant.applied;
  const configured = !!applied && project?.id === applied.workspace_id;
  const stage: Stage = runId && (workspaceVersion === undefined || !applied || workspaceVersion >= applied.version) && assistant.session?.applied.length ? 'running' : configured ? 'configured' : 'proposed';

  // The board: open for each new plan version until the user closes it.
  const [boardClosed, setBoardClosed] = useState<number>();
  const board = live && !!plan && boardClosed !== plan.version;
  const [viewing, setViewing] = useState<number>();
  useEffect(() => { setViewing(undefined); }, [plan?.version]);
  const viewedVersion = viewing ?? plan?.version;

  // The immutable compiled scene supplies both proposal geometry and the
  // baseline for comparing an applied plan with subsequent Form edits.
  const [preview, setPreview] = useState<PlanPreview & { version: number }>(), [previewPlan, setPreviewPlan] = useState<PlanVersion>(), [previewError,setPreviewError]=useState<string>();
  useEffect(() => {
    setPreview(undefined); setPreviewPlan(undefined); setPreviewError(undefined);
    if (!live || !sessionId || !plan || viewedVersion === undefined || viewedVersion===plan.version && !plan.compile.ok) return;
    const abort = new AbortController();
    void Promise.all([api.preview(sessionId, viewedVersion, abort.signal), viewedVersion === plan.version ? Promise.resolve(plan) : api.plan(sessionId, viewedVersion, abort.signal)])
      .then(([value, version]) => { if(!abort.signal.aborted) {setPreview({ ...value, version: viewedVersion }); setPreviewPlan(version);} })
      .catch(problem => { if (!abort.signal.aborted) setPreviewError(problemText(problem)); });
    return () => abort.abort();
  }, [api, live, sessionId, viewedVersion, plan?.version, plan?.compile.ok, stage]);

  // Hover, sequence playback and reveal drive the 3D layer.
  const [focus, setFocus] = useState<string[]>([]);
  const [reveal, setReveal] = useState<{ key: string; version: number }>();
  const changes = useMemo(() => applied && project && preview?.version===applied.plan_version
    ? draftChanges(preview.scenario,project.scenario) : undefined,[applied,project?.scenario,preview]);
  const zh = locale === 'zh-CN';
  const overlay = useMemo<PreviewOverlay | undefined>(() => {
    if (!live || !board) return undefined;
    if (stage === 'proposed' && preview && previewPlan) {
      const value = planOverlay(previewPlan.scene, preview.scenario, previewPlan.diff_base, 'proposed', zh);
      return { ...value, focus, frame: { key: `plan-${previewPlan.version}`, ids: revealOrder(value) } };
    }
    // Changed draft objects are drawn from the current project. Historical
    // marker positions and fleet counts must not overlay that current scene.
    if (stage === 'configured' && plan && project && changes?.length===0) {
      const value = planOverlay(plan.scene, project.scenario as Record<string, any>, applied ? { added: applied.report.created, changed: applied.report.changed, removed: applied.report.removed, entities: applied.report.entities } : plan.diff_base, 'configured', zh);
      // The reveal also frames what changed, so the materialize sweep starts where the user is looking.
      return { ...value, focus, ...(reveal && reveal.version === applied?.version ? { reveal: { key: reveal.key, order: revealOrder(value) }, frame: { key: reveal.key, ids: revealOrder(value) } } : {}) };
    }
    return undefined;
  }, [live, board, stage, preview, previewPlan, plan, project?.revision, applied, focus, reveal, zh, changes]);
  const previewWorkspace = stage === 'proposed' && preview && live && board && !applied && sessionId
    ? proposalWorkspace(project,preview,sessionId,!!previewPlan?.operations.some(op=>op.op==='set_region')) : undefined;
  const boardAssistant: AssistantSession = {...assistant,planPreview:preview,
    ...(applied && project ? {draftState:{changes,error:previewError,dirty:project.dirty,latestVersion:project.latest_version?.version}} : {})};

  // After Confirm: the real project is reloaded by the workbench; the reveal plays once.
  const applyDone = useCallback((record: ApplyRecord) => {
    setReveal({ key: `applied-${record.version}-${Date.now()}`, version: record.version });
  }, []);

  // Story: expected vs recorded, refreshed while the run is live.
  const [story, setStory] = useState<Story>(), [storyError, setStoryError] = useState<string>();
  useEffect(() => {
    setStory(undefined); setStoryError(undefined);
    if (!live || !sessionId || !runId || stage !== 'running') return;
    const abort = new AbortController();
    let timer = 0;
    const load = async () => {
      try { const value = await api.story(sessionId, runId, abort.signal, locale); if (!abort.signal.aborted) { setStory(value); setStoryError(undefined); } }
      catch (problem) { if (!abort.signal.aborted) setStoryError(problemText(problem)); }
      if (!abort.signal.aborted && !terminal) timer = window.setTimeout(load, 4000);
    };
    void load();
    return () => { abort.abort(); clearTimeout(timer); };
  }, [api, live, sessionId, runId, stage, terminal, locale]);

  // Form ↔ Conversation: both panels stay mounted while the rewire plays.
  const lastSection = useRef('controls');
  const switchMode = useCallback((to: EditMode, origin: Origin, section?: string) => {
    if (morph) return;
    setMorph({ from: to === 'chat' ? 'form' : 'chat', to, origin });
    update({ panel: to === 'chat' ? 'assistant' : section ?? lastSection.current });
  }, [morph, update]);
  useLayoutEffect(() => {
    if (!morph) return;
    const from = document.querySelector<HTMLElement>('[data-morph="out"]'), to = document.querySelector<HTMLElement>('[data-morph="in"]');
    if (!from || !to) { setMorph(undefined); return; }
    const abort = new AbortController();
    // `sessionStorage['aas-morph-scale']` slows the rewire down for design review and demos.
    const scale = Number(sessionStorage.getItem('aas-morph-scale')) || 1;
    void rewire({ from, to, origin: morph.origin, palette: { from: PALETTE[morph.from], to: PALETTE[morph.to] }, durationScale: scale, signal: abort.signal })
      .then(() => { if (!abort.signal.aborted) setMorph(undefined); });
    return () => abort.abort();
  }, [morph]);

  return {
    api, connection, assistant:boardAssistant, error:error ?? previewError, stage, board, closeBoard: () => setBoardClosed(plan?.version), openBoard: () => setBoardClosed(undefined),
    live, viewing, setViewing, overlay, previewWorkspace, focus, setFocus, story, storyError, applyDone, morph, switchMode, lastSection,
  };
}
export type AssistantMode = ReturnType<typeof useAssistantMode>;
