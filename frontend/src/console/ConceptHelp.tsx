import { Popover } from 'antd';
import { Link } from 'react-router-dom';
import './concept-help.css';

/** Short concept explanations; links resolve to the repository's actual guides. */
export function ConceptHelp({ topic, description, guide }: { topic: string; description: string; guide: string }) {
  return <Popover trigger={['click']} title={topic} content={<div className="console-concept-copy"><p>{description}</p><Link to={`/studio?guide=${encodeURIComponent(`docs/platform/${guide}`)}`} target="_blank" rel="noreferrer">Read the guide →</Link></div>}>
    <button type="button" className="console-concept-help" aria-label={`Help: ${topic}`} title={`About ${topic}`}><span aria-hidden="true">?</span></button>
  </Popover>;
}
