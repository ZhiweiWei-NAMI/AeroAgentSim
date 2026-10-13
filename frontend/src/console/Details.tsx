import { useCallback, useEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { useI18n } from '../i18n/I18nProvider';
import { readableLabel } from '../pages/inspection-format';
import './details.css';

export interface DetailsProps {
  /** Panel title; defaults to 'Details'. */
  title?: string;
  /** Trigger button label; defaults to 'Details'. */
  buttonLabel?: string;
  children: ReactNode;
}

/**
 * Reusable workbench details dialog.
 *
 * Children are lazily rendered on first open and then retained to preserve editing buffers, so heavy or raw
 * JSON content never enters the default DOM. Focus returns to the trigger on
 * close (button or Escape). Styling hook: `.console-details`.
 */
export function Details({ title = 'Details', buttonLabel = 'Details', children }: DetailsProps) {
  const { t, locale } = useI18n(), L = (en: string, zh: string) => locale === 'zh-CN' ? zh : en;
  const heading = L(`${t(title)} · details`, `${t(title)} · 详情`);
  const [open, setOpen] = useState(false);
  const [visited, setVisited] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const dialogRef = useRef<HTMLElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);

  const close = useCallback(() => {
    setOpen(false);
    // Return focus to the trigger whether closed by button, mask or Escape.
    requestAnimationFrame(() => triggerRef.current?.focus());
  }, []);

  useEffect(() => {
    if (!open) return;
    closeRef.current?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); close(); }
      if (event.key === 'Tab') {
        const fields = [...(dialogRef.current?.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), textarea:not(:disabled), select:not(:disabled), a[href], summary, [tabindex="0"]') ?? [])].filter(node => {
          const disclosure=node.closest('details:not([open])');
          return !node.closest('[hidden]') && (!disclosure || node.tagName==='SUMMARY' && disclosure.firstElementChild===node);
        });
        const first = fields[0], last = fields.at(-1);
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
      }
    };
    document.addEventListener('keydown', onKeyDown, true);
    return () => document.removeEventListener('keydown', onKeyDown, true);
  }, [open, close]);

  return (
    <span className="console-details">
      <button
        ref={triggerRef}
        type="button"
        className="wb-btn small ghost"
        aria-label={t(buttonLabel)}
        data-testid="details-trigger"
        aria-haspopup="dialog"
        aria-expanded={open}
        onClick={() => { setVisited(true); open ? close() : setOpen(true); }}
      >
        {t(buttonLabel)}
      </button>
      {visited && createPortal(<div className="wb-details-layer" hidden={!open} onMouseDown={event => { if(event.target === event.currentTarget)close(); }}>
        <section ref={dialogRef} className="wb-details-dialog wb-panel wb-glass" role={open ? 'dialog' : undefined} aria-modal={open ? true : undefined} aria-label={heading}>
          <header className="wb-panel-head"><h3>{heading}</h3><button ref={closeRef} type="button" className="wb-x" aria-label={L('Close','关闭')} onClick={close}>✕</button></header>
          <div className="wb-panel-body">{children}</div>
        </section>
      </div>, triggerRef.current?.closest('.wb') ?? document.body)}
    </span>
  );
}

/** Field layout for recorded values; nested records stay readable without JSON escaping. */
export function RecordFields({ record }: { record: unknown }) {
  const { locale } = useI18n(), L = (en: string, zh: string) => locale === 'zh-CN' ? zh : en;
  const labels: Record<string, string> = {
    id: L('ID','标识'), command_id: L('Command ID','命令标识'), commandId: L('Command ID','命令标识'),
    status: L('Status','状态'), disposition: L('Admission','受理状态'), kind: L('Kind','类型'),
    submitted_at: L('Submitted at','提交时刻'), requested_at: L('Requested at','请求时刻'), ns: L('Time (ns)','时间（纳秒）'), microstep: L('Microstep','微步'),
    execution_status: L('Execution status','执行状态'), execution_result: L('Execution result','执行结果'), execution_cut: L('Execution record','执行日志位置'),
    target_cut: L('Admission record','受理日志位置'), target: L('Target','目标'), result: L('Result','结果'), detail: L('Details','详情'),
    injection_point: L('Event','事件'), code: L('Code','代码'), idempotency_key: L('Request identity','请求标识'), stream_id: L('Input source','输入源'),
  };
  const statuses: Record<string,string> = {
    applied:L('Applied','已执行'),succeeded:L('Succeeded','成功'),failed:L('Failed','失败'),rejected:L('Rejected','已拒绝'),
    accepted:L('Accepted','已受理'),admitted:L('Admitted','已受理'),delayed:L('Scheduled for later','稍后执行'),
    running:L('Running','运行中'),paused:L('Paused','已暂停'),waiting_for_input:L('Waiting for input','等待输入'),
  };
  if (record && typeof record === 'object' && !Array.isArray(record)) {
    return <dl className="wb-record-fields">{Object.entries(record).map(([key,value]) => <div key={key}><dt>{labels[key] ?? readableLabel(key,locale)}</dt><dd>{typeof value==='string' && ['status','execution_status','disposition'].includes(key)
      ? <span>{statuses[value] ?? value}</span> : key==='injection_point' && typeof value==='string'
        ? <span title={value}>{readableLabel(value,locale)}</span> : <RecordFields record={value}/>}</dd></div>)}</dl>;
  }
  if (Array.isArray(record)) return <ol className="wb-record-list">{record.map((value,index)=><li key={index}><RecordFields record={value}/></li>)}</ol>;
  const text = record === null ? L('Null (recorded)','空值（已记录）') : record === undefined ? L('Not recorded','未记录') : typeof record === 'boolean' ? L(record ? 'True':'False',record ? '真':'假') : String(record);
  return <span className="wb-mono">{text}</span>;
}
