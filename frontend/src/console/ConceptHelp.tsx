import { Popover } from 'antd';
import { Link } from 'react-router-dom';
import './concept-help.css';
import { useI18n } from '../i18n/I18nProvider';

/** Short concept explanations; links resolve to the repository's actual guides. */
export function ConceptHelp({ topic, description, guide }: { topic: string; description: string; guide: string }) {
  const { t } = useI18n();
  return <Popover trigger={['click']} title={t(topic)} content={<div className="console-concept-copy"><p>{t(description)}</p><Link to={`/studio?guide=${encodeURIComponent(`docs/platform/${guide}`)}`} target="_blank" rel="noreferrer">{t('Read the guide →')}</Link></div>}>
    <button type="button" className="console-concept-help" aria-label={t('Help: {topic}', { topic: t(topic) })} title={t('About {topic}', { topic: t(topic) })}><span aria-hidden="true">?</span></button>
  </Popover>;
}
