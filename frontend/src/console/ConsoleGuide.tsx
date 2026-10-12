import { useEffect, useState } from 'react';
import { useLocation, Link } from 'react-router-dom';
import { PageHeader, PageState } from './PageState';
import { useI18n } from '../i18n/I18nProvider';
import { loadGuideSource, resolveGuidePath } from './guides';
import './console-guide.css';

export { loadGuideSource, resolveGuidePath } from './guides';

type Translator = (key: string) => string;

/** Authored help stays concise; command names and source documents are not rewritten. */
function guideTitle(guide: string | null, t: Translator): string {
  switch (guide) {
    case 'getting-started/install.md': return t('Install');
    case 'getting-started/quickstart.md': return t('Run the traffic-accident demo');
    case 'getting-started/first-scenario.md': return t('Your first scenario');
    case 'concepts/architecture.md': return t('Architecture');
    case 'concepts/plugins.md': return t('Plugins');
    case 'concepts/behaviours.md': return t('Behaviours and chains');
    case 'concepts/agents.md': return t('Agents');
    case 'concepts/aerograph.md': return t('AeroGraph and scenario registries');
    case 'concepts/runs.md': return t('Runs, replay and provenance');
    case 'concepts/predicates.md': return t('Predicates');
    case 'concepts/views.md': return t('Views: the console');
    case 'guides/agents.md': return t('Guide: Agents (decision engines and live LLMs)');
    case 'guides/predicates.md': return t('Write a predicate');
    case 'guides/algorithms.md': return t('Low-altitude algorithms');
    case 'guides/sumo.md': return t('Guide: SUMO road traffic');
    case 'guides/external-events.md': return t('Guide: External events and live ingress');
    case 'guides/visualization.md': return t('Guide: Visualization and the web console');
    case 'guides/behaviours.md': return t('Guide: Behaviour packages');
    case 'guides/weather.md': return t('Guide: Weather and environmental profiles');
    case 'guides/containers.md': return t('Native simulator containers');
    case 'guides/experiments.md': return t('Compare two allocation algorithms in one command');
    case 'guides/templates.md': return t('Make your own template in 10 minutes');
    case 'guides/plugins.md': return t('Guide: Writing engine plugins');
    case 'guides/parcel-city-placement.md': return t('Parcel-delivery city placement');
    case 'guides/lowalt-rules.md': return t('Edit the low-altitude rules');
    case 'guides/px4-gazebo.md': return t('Guide: PX4/Gazebo flight simulation');
    case 'reference/behaviour-package.md': return t('Behaviour packages');
    case 'reference/viewer-feed.md': return t('Viewer feed reference');
    case 'reference/cli.md': return t('CLI reference');
    case 'reference/scenario.md': return t('Scenario reference');
    case 'reference/predicate-dialect.md': return t('Predicate dialect reference');
    case 'reference/http-api.md': return t('HTTP API reference');
    case 'examples/traffic-accident.md': return t('Traffic-accident experiment');
    case 'examples/city-day.md': return t('City day');
    default: return t('Console guide');
  }
}

function guideSummary(guide: string | null, t: Translator): string {
  switch (guide) {
    case 'getting-started/install.md':
      return t('Install the Python backend and build the web console first. The source lists exact commands and optional simulator dependencies.');
    case 'getting-started/quickstart.md':
    case 'examples/traffic-accident.md':
      return t('Start with the traffic-accident demo: a vehicle reports an accident, a UAV receives the task, then captures and uploads a picture. Inspect recorded events to see each step.');
    case 'getting-started/first-scenario.md':
    case 'reference/scenario.md':
    case 'guides/templates.md':
      return t('A scenario is the recipe for a simulation: entities, starting values, plugins and rules. Start small, validate the recipe in Studio, then create a run.');
    case 'concepts/aerograph.md':
      return t('A type describes a kind of entity. Fields store values, relations link entities, and parent types pass their fields to children. A registry snapshot contains the definitions a scenario needs.');
    case 'concepts/architecture.md':
      return t('One shared kernel controls simulation time and recorded state. Plugins handle different tasks, but each entity field has one assigned writer. Replay reads the record without running those plugins again.');
    case 'concepts/plugins.md':
    case 'guides/plugins.md':
      return t('A plugin computes part of the simulation, such as motion or weather. Bindings assign the fields it may update. Check the field writer before expecting a value to change.');
    case 'concepts/predicates.md':
    case 'guides/predicates.md':
    case 'reference/predicate-dialect.md':
      return t('A predicate asks a yes/no question about recorded values, for example “is speed below the limit?”. Missing or invalid inputs produce an unknown result, not false. Rules can react when a known result changes.');
    case 'concepts/behaviours.md':
    case 'guides/behaviours.md':
    case 'reference/behaviour-package.md':
    case 'guides/lowalt-rules.md':
      return t('An event reports something that happened. A chain is a sequence of named steps: a trigger starts a transition, a condition checks whether it is allowed, and actions do the work. A behaviour package groups these rules.');
    case 'concepts/agents.md':
    case 'guides/agents.md':
      return t('An agent reads observations and proposes decisions. Scripted replies and live language-model calls are different modes; live calls need explicit configuration. Inspect action receipts to check what actually happened.');
    case 'concepts/runs.md':
      return t('A workspace is an editable recipe; a run is one execution of it. Replay shows recorded results. Pausing playback stops the picture, while pausing a simulation stops its time advancement.');
    case 'concepts/views.md':
    case 'guides/visualization.md':
    case 'reference/viewer-feed.md':
      return t('Use Studio to edit a scenario, AeroGraph to understand its types, Runs to manage executions, and Inspect to explore recorded results. A smooth animation is a display aid, not a new recorded fact.');
    case 'guides/experiments.md':
    case 'guides/algorithms.md':
      return t('Compare algorithms with the same scenario and named settings. Keep each run’s record and inspect the measured results; changing several settings at once makes the cause of a difference harder to understand.');
    case 'guides/sumo.md':
      return t('SUMO supplies road-traffic simulation through an adapter. Check the required native installation, road network and writer bindings in the source before starting this profile.');
    case 'guides/px4-gazebo.md':
      return t('PX4 and Gazebo provide a native flight-simulation profile. This needs its own simulator setup and adapter configuration; it is not the lightweight demo’s default motion model.');
    case 'guides/containers.md':
      return t('Containers package native simulator dependencies. They do not replace the scenario’s declared clocks, connections or writer bindings. Follow the source for the required startup configuration.');
    case 'guides/external-events.md':
      return t('External inputs enter through named streams. Their clock mapping and progress determine when simulation time may advance. Late inputs follow the declared policy instead of silently changing past results.');
    case 'guides/weather.md':
      return t('A weather profile supplies environmental values used by other plugins. Check units, the selected profile and writer bindings before interpreting its effect on motion.');
    case 'guides/parcel-city-placement.md':
    case 'examples/city-day.md':
      return t('Place the scenario’s entities in the city before running it. Check locations, routes and task assignments in Studio, then inspect a run to see how those starting choices affect the result.');
    case 'reference/cli.md':
      return t('The command-line interface installs, runs, serves and replays scenarios. Copy command names and flags exactly from the English source; they are not translated interface labels.');
    case 'reference/http-api.md':
      return t('The HTTP API connects the console and other clients to the backend. Keep endpoint paths and JSON keys unchanged, and check the returned status or error rather than assuming a request succeeded.');
    default:
      return t('Read the guide for the concept, then try one small change in Studio and validate it before running. Keep command names, identifiers and units exactly as written in the source.');
  }
}

export default function ConsoleGuide() {
  const { t, locale } = useI18n();
  const { pathname, search } = useLocation();
  const query = new URLSearchParams(search);
  const requested = query.get('guide') ?? pathname;
  const guide = resolveGuidePath(requested);
  const [text, setText] = useState<string>(), [error, setError] = useState('');
  const [showSource, setShowSource] = useState(false);
  const chinese = locale.startsWith('zh');
  const studioQuery = new URLSearchParams();
  for (const key of ['api', 'workspace']) if (query.has(key)) studioQuery.set(key, query.get(key)!);
  const studioLink = studioQuery.size ? `/studio?${studioQuery}` : '/studio';
  useEffect(() => {
    let active = true; setText(undefined); setError(''); setShowSource(false);
    if (!guide) { setError('This guide is not available in this build.'); return; }
    loadGuideSource(guide).then(value => { if (active) setText(String(value)); }).catch(problem => { if (active) setError(String(problem)); });
    return () => { active = false; };
  }, [guide]);
  return <div className="console-page console-guide-page">
    <PageHeader eyebrow={t('Documentation')} title={guideTitle(guide, t)}
      actions={<Link className="console-btn" to={studioLink}>{t('Back to Studio')}</Link>} />
    {error ? <PageState kind="error" title={t('Could not open the guide')} description={t(error)} />
      : text === undefined ? <PageState kind="loading" title={t('Loading guide')} />
      : <>
        <section className="console-guide-summary" aria-labelledby="console-guide-summary-title">
          <h2 id="console-guide-summary-title">{t('In plain language')}</h2>
          <p>{guideSummary(guide, t)}</p>
          <p className="console-guide-tip">{t('Try one change at a time. Validate in Studio before running, and inspect recorded values and receipts to check the result.')}</p>
        </section>
        <section className="console-guide-source" aria-label={t('Guide source (English)')}>
          <p>{chinese
            ? t('The original guide is in English. The Chinese summary above explains the basics; open the source for exact commands and configuration keys.')
            : t('This is the original English Markdown source. Commands, identifiers and configuration keys are kept unchanged.')}</p>
          {chinese && <button type="button" className="console-btn" aria-expanded={showSource} aria-controls="console-guide-source-text" onClick={() => setShowSource(value => !value)}>
            {showSource ? t('Hide English source') : t('Show English source')}
          </button>}
          {(!chinese || showSource) && <pre id="console-guide-source-text" className="console-guide-text">{text}</pre>}
        </section>
      </>}
  </div>;
}
