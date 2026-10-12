import type { CSSProperties } from 'react';
import { useI18n } from '../i18n/I18nProvider';
import type { Expectation, Story, StoryItem } from './api';
import { Icon } from './icons';
import { clock, pick } from './present';

export const storySeconds = (atNs: string | number | null | undefined, startNs: string | undefined) => {
  if (atNs === null || atNs === undefined || !startNs) return undefined;
  try { return Number(BigInt(atNs) - BigInt(startNs)) / 1e9; } catch { return undefined; }
};

/** Expected vs recorded, honestly: a missing step shows the recorded reason, never a replayed success. */
export function RunStory({ story, startNs, terminal, expectations, onSeek, onAsk, error }: {
  story?: Story; startNs?: string; terminal: boolean; expectations?: Expectation[]; onSeek?: (cut: number) => void; onAsk?: (item: StoryItem) => void; error?: string;
}) {
  const { locale } = useI18n(), zh = locale === 'zh-CN', L = (en: string, cn: string) => zh ? cn : en;
  if (error && !story) return <p className="as-note bad">{error}</p>;
  if (!story) return <div className="as-story is-loading" data-testid="as-story"><span className="as-spin" /> {L('Reading the recorded events…', '正在读取记录的事件…')}</div>;
  const items = story.expectations, happened = items.filter(item => item.status === 'happened').length, missed = items.filter(item => item.status === 'missed').length;
  // A conditional branch that was not taken is neither a success nor a failure: it leaves the score.
  const skipped = items.filter(item => item.status === 'not_taken').length, total = items.length - skipped;
  const text = (item: StoryItem) => (zh ? item.text_zh : undefined) ?? item.text;
  const planned = new Map((expectations ?? []).map(row => [row.id, row]));
  const ended = story.terminal ?? terminal;
  // Chronological: recorded moments first by time, then expected ones by their planned time.
  const when = (item: StoryItem) => storySeconds(item.at_ns, startNs) ?? planned.get(item.id)?.approx_s ?? Infinity;
  const ordered = items.map((item, index) => ({ item, index })).sort((a, b) => when(a.item) - when(b.item) || a.index - b.index).map(row => row.item);
  return <div className="as-story" data-testid="as-story">
    <div className="as-story-score">
      <strong className="wb-mono">{happened}<small>/{total}</small></strong>
      <div>
        <span>{ended ? missed ? L(`${missed} expected step${missed === 1 ? '' : 's'} did not happen`, `${missed} 个预期步骤未发生`) : happened === total ? L('Everything the plan expected happened', '方案预期的步骤全部发生') : L('Run ended', '运行已结束') : L('Watching the run', '正在跟踪运行')}</span>
        <div className="as-story-bar" aria-hidden>{items.filter(item => item.status !== 'not_taken').map(item => <i key={item.id} className={`is-${item.status}`} />)}</div>
        {skipped > 0 && <small className="as-story-skipped">{L(`${skipped} conditional branch${skipped === 1 ? '' : 'es'} not taken`, `${skipped} 个条件分支未触发`)}</small>}
      </div>
    </div>
    <ol className="as-story-list">{ordered.map((item, index) => {
      const seconds = storySeconds(item.at_ns, startNs), plan = planned.get(item.id), approx = plan?.approx_s;
      return <li key={item.id} className={`is-${item.status} is-${plan?.layer ?? 'behaviour'}`} style={{ '--i': index } as CSSProperties} data-testid={`as-story-${item.status}`}>
        <span className="as-story-mark">{item.status === 'happened' ? <Icon name="check" size={11} /> : item.status === 'missed' ? <Icon name="cross" size={11} /> : item.status === 'not_taken' ? <Icon name="branch" size={12} /> : <span className="as-story-wait" />}</span>
        <time className="wb-mono">{seconds !== undefined ? clock(seconds) : approx !== undefined && approx !== null ? `≈${clock(approx)}` : '—'}</time>
        <div className="as-story-text">
          <span>{text(item)}{item.status === 'not_taken' && <em className="as-story-tag">{L('branch not taken', '分支未触发')}</em>}</span>
          {item.progress && (item.status !== 'happened' || item.progress.configured) && <span className="as-story-progress" aria-label={L(`${item.progress.done} of ${item.progress.total}`, `${item.progress.done}/${item.progress.total}`)}>
            <i style={{ '--p': item.progress.done / Math.max(1, item.progress.total) } as CSSProperties} /><b className="wb-mono">{item.progress.done}/{item.progress.total}</b>
            {item.progress.configured ? <small>{L(`released · ${item.progress.configured} configured`, `已发布 · 共配置 ${item.progress.configured}`)}</small> : null}
          </span>}
          {(item.status === 'missed' || item.status === 'not_taken') && item.explanation && <p className="as-why"><b>{L('Recorded:', '记录显示：')}</b> {(zh ? item.explanation.text_zh : undefined) ?? item.explanation.text}</p>}
          {item.evidence ? <p className="as-evidence"><span>{pick(item.evidence, zh) || item.evidence.schema}</span></p> : null}
        </div>
        <span className="as-story-act">
          {typeof item.cut === 'number' && onSeek && <button className="wb-btn small" onClick={() => onSeek(item.cut!)} title={L('Jump to this moment', '跳到此时刻')}><Icon name="rewind" size={11} /></button>}
          {item.status === 'missed' && onAsk && <button className="wb-btn small" onClick={() => onAsk(item)} data-testid="as-ask-why">{L('Ask why', '问原因')}</button>}
        </span>
      </li>;
    })}</ol>
    {story.events.length > 0 && <details className="as-story-events">
      <summary>{L(`Key recorded events (${story.events.length})`, `关键记录事件（${story.events.length}）`)}</summary>
      <ol>{story.events.map((event, index) => { const seconds = storySeconds(event.at_ns, startNs); return <li key={`${event.at_ns}-${index}`}>
        <button onClick={() => event.cut !== undefined && onSeek?.(event.cut)} disabled={event.cut === undefined}><time className="wb-mono">{seconds !== undefined ? clock(seconds) : ''}</time><span>{(zh ? event.text_zh : undefined) ?? event.text}</span></button>
      </li>; })}</ol>
    </details>}
  </div>;
}
