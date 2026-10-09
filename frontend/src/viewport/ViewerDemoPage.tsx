import { useEffect, useState } from 'react';
import { Alert, Button, Select, Slider, Space, Switch, Tag, Typography } from 'antd';
import { Link } from 'react-router-dom';
import type { EntityKey } from '../contracts/viewer-feed';
import { useI18n } from '../i18n/I18nProvider';
import { demoFeed } from './demo-feed';
import { FeedStore } from './feed-store';
import { PlaybackClock } from './clock';
import { entityId } from './bindings';
import { ViewerPresentation } from './ViewerPresentation';
import { EntityInspector } from './EntityInspector';
import type { Quality } from './pipeline';
import type { CameraMode } from './viewport';
import './viewer.css';

export default function ViewerDemoPage() {
  const { locale, setLocale, t } = useI18n();
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
  if (!session) return <div className="viewer-loading">{error ? <Alert type="error" message={error} /> : t('viewerLoading')}</div>;
  const { store, clock } = session;
  let currentIndex = 0;
  for (let i = 0; i < store.commits.length; i++) { if (BigInt(store.commits[i].at.ns) > BigInt(clock.ns)) break; currentIndex = i; }
  if (!clock.playing && commitCut !== undefined) currentIndex = store.commits.findIndex(commit => commit.commitIndex === commitCut);
  const current = store.commits[currentIndex];
  const elapsed = Number(BigInt(clock.ns) - BigInt(store.header.start.ns)) / 1e9;
  const seek = (index: number) => { clock.pause(); clock.seek(store.commits[index].at.ns); setCommitCut(store.commits[index].commitIndex); store.seek(clock.ns, store.commits[index].commitIndex); tick(); };
  return <div className="viewer-demo">
    <header className="viewer-header">
      <div><Link to="/" className="viewer-brand">AeroAgentSim <span> / {t('viewerTitle')}</span></Link><div className="viewer-subtitle">{t('viewerSubtitle')}</div></div>
      <Space><Tag color="gold">{t('viewerSynthetic')}</Tag><Select aria-label="Language" value={locale} onChange={setLocale} options={[{ value: 'en-US', label: 'English' }, { value: 'zh-CN', label: '简体中文' }]} /></Space>
    </header>
    <main className="viewer-main">
      <section className="viewer-stage">
        <ViewerPresentation store={store} clock={clock} selected={selected} mode={mode} quality={quality} trails={trails} commitCut={commitCut} onTick={tick} onSelect={setSelected}
          onError={error => setError(String(error))} onQuality={(quality, fps) => { setQuality(quality); setFps(fps); }} />
        <div className="viewport-toolbar">
          <Select aria-label="Camera mode" value={mode} onChange={setMode} options={['orbit', 'follow', 'chase'].map(value => ({ value, label: t(`viewerCamera_${value}`) }))} />
          <Select aria-label="Quality" value={quality} onChange={setQuality} options={['low', 'med', 'high'].map(value => ({ value, label: t(`viewerQuality_${value}`) }))} />
          <Space><Switch size="small" aria-label="Trails" checked={trails} onChange={setTrails} /><span>{t('viewerTrails')}</span></Space>
        </div>
        <div className="viewport-caption"><span className="caption-dot" />{t('viewerSpatialCount', { count: 50 })}<span>ENU · m {fps !== undefined && ` · ${fps.toFixed(0)} FPS`}</span></div>
        {error && <Alert className="viewport-error" type="error" message={error} showIcon />}
      </section>
      <aside className="viewer-sidebar">
        <div className="inspector-selector"><Typography.Text type="secondary">{t('viewerInspector')}</Typography.Text>
          <Select showSearch aria-label="Entity" value={entityId(selected)} onChange={id => { const entity = store.entities.get(id); if (entity) setSelected(entity.key); }}
            options={[...store.entities].map(([id, entity]) => ({ value: id, label: entity.key.id }))} />
        </div><EntityInspector store={store} selected={selected} />
      </aside>
    </main>
    <footer className="viewer-timeline">
      <Button type="primary" aria-label={clock.playing ? 'Pause' : 'Play'} onClick={() => { setCommitCut(undefined); if (clock.playing) clock.pause(); else clock.play(); tick(); }}>{t(clock.playing ? 'viewerPause' : 'viewerPlay')}</Button>
      <Button aria-label="Previous commit" onClick={() => seek(Math.max(0, currentIndex - 1))}>‹</Button>
      <Button aria-label="Next commit" onClick={() => seek(Math.min(store.commits.length - 1, currentIndex + 1))}>›</Button>
      <div className="timeline-track"><Slider aria-label="Commit timeline" min={0} max={store.commits.length - 1} value={currentIndex} onChange={seek} tooltip={{ formatter: index => `#${index}` }} />
        <div className="timeline-metadata"><span>{t('viewerCommit')} #{current.commitIndex} · μ{current.at.microstep}</span><span>{current.at.ns} ns</span><span>{elapsed.toFixed(2)} / 24.00 s</span></div>
      </div>
      <Select aria-label="Playback speed" value={clock.speed} onChange={speed => { clock.setSpeed(speed); tick(); }} options={[0.25, 0.5, 1, 2, 4].map(value => ({ value, label: `${value}×` }))} />
    </footer>
  </div>;
}
