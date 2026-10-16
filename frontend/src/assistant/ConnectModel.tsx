import { useState } from 'react';
import { useI18n } from '../i18n/I18nProvider';
import type { Connection } from './api';
import { Icon } from './icons';
import { bi } from './present';

/** Connect the model the assistant (and agent-decided behaviours) will use. */
export function ConnectModel({ connection, error, saving, onSave, onTest, onDone, compact = false }: {
  connection?: Connection; error?: string; saving: boolean; compact?: boolean;
  onSave: (body: { base_url: string; model: string; api_key?: string; clear_key?: boolean }) => Promise<unknown>; onTest: () => Promise<void>; onDone?: () => void;
}) {
  const { locale } = useI18n(), zh = locale === 'zh-CN', L = (en: string, cn: string) => zh ? cn : en;
  const [editing, setEditing] = useState(!connection?.configured);
  const [baseUrl, setBaseUrl] = useState(connection?.base_url ?? 'http://127.0.0.1:8788/v1');
  const [model, setModel] = useState(connection?.model ?? '');
  const [key, setKey] = useState(''), [failed, setFailed] = useState<string>();
  const ok = connection?.configured && connection.status === 'ok';
  const submit = async () => {
    setFailed(undefined);
    try {
      const value = await onSave({ base_url: baseUrl.trim(), model: model.trim(), ...(key ? { api_key: key } : {}) }) as Connection; setKey('');
      if (value?.status === 'ok') { setEditing(false); onDone?.(); }
      else setFailed(value?.detail ? bi(value.detail, zh) : L('The model did not answer the test request. Check the endpoint, model name and key.', '模型未响应测试请求，请检查接口地址、模型名称和密钥。'));
    } catch { /* The error is shown below. */ }
  };
  return <section className={`as-connect${compact ? ' is-compact' : ''}`} data-testid="as-connect" aria-label={L('Model connection', '模型连接')}>
    {!compact && <div className="as-connect-mark"><Icon name="plug" size={22} /></div>}
    <h3>{L('Connect a model', '连接模型')}</h3>
    <p>{L('The assistant uses this model to read what the platform can do and to draft plans with you. Nothing changes in your scene until you confirm a plan.', '助手通过这个模型了解平台能力，并与你一起起草方案。在你确认方案之前，场景不会有任何改动。')}</p>
    {connection?.configured && !editing ? <div className="as-connect-current">
      <span className={`as-led ${connection.status}`} />
      <div><b>{connection.model}</b><small>{connection.base_url}{connection.source === 'operator' ? L(' · set by the operator', ' · 由管理员配置') : ''}{connection.has_key ? L(' · key stored', ' · 已保存密钥') : ''}</small>
        {connection.status === 'error' && connection.detail && <small className="as-bad">{bi(connection.detail, zh)}</small>}</div>
      <button className="wb-btn small" disabled={saving} onClick={() => void onTest()}>{saving ? L('Testing…', '测试中…') : L('Test', '测试')}</button>
      <button className="wb-btn small ghost" onClick={() => setEditing(true)}>{L('Change', '更换')}</button>
      {ok && onDone && <button className="wb-btn small primary" onClick={onDone}>{L('Continue', '继续')}</button>}
    </div> : <form className="as-connect-form" onSubmit={event => { event.preventDefault(); void submit(); }}>
      <label>{L('Endpoint (OpenAI-compatible)', '接口地址（兼容 OpenAI）')}<input value={baseUrl} onChange={event => setBaseUrl(event.target.value)} placeholder="https://…/v1" spellCheck={false} data-testid="as-connect-url" /></label>
      <label>{L('Model', '模型')}<input value={model} onChange={event => setModel(event.target.value)} placeholder="glm-5.3-flashx" spellCheck={false} data-testid="as-connect-model" /></label>
      <label>{L('API key (optional)', 'API 密钥（可选）')}<input type="password" value={key} onChange={event => setKey(event.target.value)} placeholder={connection?.has_key ? L('Stored — leave empty to keep it', '已保存，留空则保持不变') : ''} autoComplete="off" data-testid="as-connect-key" />
        <small>{L('Kept on this server and never shown again.', '仅保存在本服务器上，不会再次显示。')}</small></label>
      <div className="as-connect-actions">
        {connection?.configured && <button type="button" className="wb-btn ghost" onClick={() => setEditing(false)}>{L('Cancel', '取消')}</button>}
        <button type="submit" className="wb-btn primary" disabled={saving || !baseUrl.trim() || !model.trim()} data-testid="as-connect-save">{saving ? L('Connecting…', '正在连接…') : error ? L('Retry', '重试') : L('Connect and test', '连接并测试')}</button>
      </div>
    </form>}
    {(error || (editing && failed)) && <p className="as-bad" role="alert" data-testid="as-connect-error">{error ?? failed}</p>}
  </section>;
}
