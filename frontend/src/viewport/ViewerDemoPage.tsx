import { useEffect, useState } from 'react';
import { Alert, Button, Select, Slider, Space, Switch, Tag, Typography } from 'antd';
import { Link } from 'react-router-dom';
import type { EntityKey } from '../contracts/viewer-feed';
import { useI18n } from '../i18n/I18nProvider';
import { demoFeed } from './demo-feed';
import { FeedStore } from './feed-store';
import { PlaybackClock } from './clock';
import { entityId, resolveBinding } from './bindings';
import { ViewerPresentation } from './ViewerPresentation';
import { EntityInspector } from './EntityInspector';
import type { Quality } from './pipeline';
import type { CameraMode } from './viewport';
import './viewer.css';

export default function ViewerDemoPage() {
  const { t } = useI18n();
  const [session, setSession] = useState<{ store: FeedStore; clock: PlaybackClock }>();
  const [selected, setSelected] = useState<EntityKey>({ id: 'object-01', generation: 0 });
  const [mode, setMode] = useState<CameraMode>('orbit');
  const [quality, setQuality] = useState<Quality>('med');
  const [trails, setTrails] = useState(true);
  const [fps, setFps] = useState<number>();
  const [commitCut, setCommitCut] = useState<number>();
  const [error, setError] = useState<string>();
  const [, refresh] = useState(0);
  const tick = () => refresh(value => value + 1);
  useEffect(() => {
    const abort = new AbortController();
    const feed = demoFeed();
    void feed.header().then(async header => {
      const store = new FeedStore(header);
      await feed.subscribe(0, commit => store.ingest(commit), abort.signal);
      if (abort.signal.aborted) return;
      const clock = new PlaybackClock(header.start.ns, header.end!.ns);
      store.seek(clock.ns); setSession({ store, clock });
    }).catch(error => { if (!abort.signal.aborted) setError(String(error)); });
    return () => abort.abort();
  }, []);
  if (!session) return <div className="viewer-loading">{error ? <Alert type="error" message={t('Unable to load the demonstration.')} description={<details><summary>{t('Technical details')}</summary><code>{error}</code></details>} /> : t('Loading authored demonstration…')}</div>;
  const { store, clock } = session;
  let currentIndex = 0;
  for (let i = 0; i < store.commits.length; i++) { if (BigInt(store.commits[i].at.ns) > BigInt(clock.ns)) break; currentIndex = i; }
  if (!clock.playing && commitCut !== undefined) {
    const index = store.commits.findIndex(commit => commit.commitIndex === commitCut);
    if (index >= 0) currentIndex = index;
  }
  const current = store.commits[currentIndex];
  const elapsed = Number(BigInt(clock.ns) - BigInt(store.header.start.ns)) / 1e9;
  const duration = Number(BigInt(clock.end) - BigInt(store.header.start.ns)) / 1e9;
  const spatialCount = [...store.entities.values()].filter(entity => resolveBinding(store.header, entity.typeId)).length;
  const seek = (index: number) => {
    const commit = store.commits[index];
    if (!commit) return;
    clock.pause(); clock.seek(commit.at.ns); setCommitCut(commit.commitIndex); store.seek(clock.ns, commit.commitIndex); tick();
  };
  return <div className="viewer-demo">
    <header className="viewer-header">
      <div><Link to="/" className="viewer-brand">AeroAgentSim <span> / {t('Journal viewer')}</span></Link><div className="viewer-subtitle">{t('Descriptor-driven scene · exact commits, smooth display')}</div></div>
      <Tag color="gold">{t('Authored demo')}</Tag>
    </header>
    <main className="viewer-main">
      <section className="viewer-stage">
        <ViewerPresentation store={store} clock={clock} selected={selected} mode={mode} quality={quality} trails={trails} commitCut={commitCut} onTick={tick} onSelect={setSelected}
          onCameraMode={setMode} onSeekCommit={(_ns, index) => setCommitCut(index)} onError={error => setError(String(error))} onQuality={(quality, fps) => { setQuality(quality); setFps(fps); }} />
        <div className="viewport-toolbar">
          <Select aria-label={t('Camera mode')} value={mode} onChange={setMode} options={[{ value: 'orbit', label: t('Orbit') }, { value: 'follow', label: t('Follow unit') }, { value: 'chase', label: t('Chase') }, { value: 'cinematic', label: t('Event director') }]} />
          <Select aria-label={t('Quality')} value={quality} onChange={setQuality} options={[{ value: 'low', label: t('Low') }, { value: 'med', label: t('Balanced') }, { value: 'high', label: t('High') }]} />
          <Space><Switch size="small" aria-label={t('Trails')} checked={trails} onChange={setTrails} /><span>{t('Trail')}</span></Space>
        </div>
        <div className="viewport-caption"><span className="caption-dot" />{t('{count} spatial entities', { count: spatialCount })}<span>{t('ENU · m')}{fps !== undefined && ` · ${t('{fps} FPS', { fps: fps.toFixed(0) })}`}</span></div>
        {error && <Alert className="viewport-error" type="error" message={t('Unable to render the view.')} description={<details><summary>{t('Technical details')}</summary><code>{error}</code></details>} showIcon />}
      </section>
      <aside className="viewer-sidebar">
        <div className="inspector-selector"><Typography.Text type="secondary">{t('Entity inspector')}</Typography.Text>
          <Select showSearch aria-label={t('Entity')} value={store.entities.has(entityId(selected)) ? entityId(selected) : undefined} placeholder={t('Select an entity')} onChange={id => { const entity = store.entities.get(id); if (entity) setSelected(entity.key); }}
            options={[...store.entities].map(([id, entity]) => ({ value: id, label: entity.key.id }))} />
        </div><EntityInspector store={store} selected={selected} />
      </aside>
    </main>
    <footer className="viewer-timeline">
      <Button type="primary" aria-label={clock.playing ? t('Pause') : t('Play')} disabled={!current} onClick={() => { setCommitCut(undefined); if (clock.playing) clock.pause(); else clock.play(); tick(); }}>{clock.playing ? t('Pause') : t('Play')}</Button>
      <Button aria-label={t('Previous commit')} disabled={!current || currentIndex === 0} onClick={() => seek(Math.max(0, currentIndex - 1))}>‹</Button>
      <Button aria-label={t('Next commit')} disabled={!current || currentIndex >= store.commits.length - 1} onClick={() => seek(Math.min(store.commits.length - 1, currentIndex + 1))}>›</Button>
      <div className="timeline-track"><Slider aria-label={t('Commit timeline')} ariaLabelForHandle={t('Commit timeline')} ariaValueTextFormatterForHandle={index => t('Commit #{index}', { index: store.commits[index]?.commitIndex ?? index })} disabled={!current} min={0} max={Math.max(0, store.commits.length - 1)} value={currentIndex} onChange={seek} tooltip={{ formatter: index => index === undefined || index === null ? '' : t('Commit #{index}', { index: store.commits[index]?.commitIndex ?? index }) }} />
        <div className="timeline-metadata"><span>{current ? t('Commit #{index} · μ{microstep}', { index: current.commitIndex, microstep: current.at.microstep }) : t('No recorded items')}</span><span>{current && t('{ns} ns', { ns: current.at.ns })}</span><span>{t('{elapsed} / {duration} s', { elapsed: elapsed.toFixed(2), duration: duration.toFixed(2) })}</span></div>
      </div>
      <Select aria-label={t('Playback speed')} value={clock.speed} onChange={speed => { clock.setSpeed(speed); tick(); }} options={[0.25, 0.5, 1, 2, 4].map(value => ({ value, label: t('{speed}×', { speed: value }) }))} />
    </footer>
  </div>;
}
