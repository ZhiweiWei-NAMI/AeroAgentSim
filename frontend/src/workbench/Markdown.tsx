import type { ReactNode } from 'react';

type LinkOpener = (href: string) => (() => void) | undefined;

/** Inline markdown: code, bold, italics, links and images. No raw HTML is ever rendered. */
function inline(text: string, imageBase?: string, openLink?: LinkOpener): ReactNode[] {
  const out: ReactNode[] = [];
  const pattern = /(`[^`]+`)|(\*\*[^*]+\*\*)|(!\[[^\]]*\]\([^)]+\))|(\[[^\]]+\]\([^)]+\))|(\*[^*]+\*)/g;
  let last = 0, match: RegExpExecArray | null, key = 0;
  while ((match = pattern.exec(text))) {
    if (match.index > last) out.push(text.slice(last, match.index));
    const token = match[0];
    if (token.startsWith('`')) out.push(<code key={key++}>{token.slice(1, -1)}</code>);
    else if (token.startsWith('**')) out.push(<strong key={key++}>{token.slice(2, -2)}</strong>);
    else if (token.startsWith('![')) {
      const [, alt, src] = /!\[([^\]]*)\]\(([^)\s]+)/.exec(token) ?? [];
      // Only images that sit beside the guide (thumbnail) can be served; others keep their alt text.
      if (imageBase && src && /^[\w.-]+\.(png|jpe?g|gif|webp)$/i.test(src)) out.push(<img key={key++} alt={alt} src={`${imageBase}/${src}`} />);
      else out.push(<em key={key++}>{alt}</em>);
    } else if (token.startsWith('[')) {
      const [, label, href] = /\[([^\]]+)\]\(([^)\s]+)/.exec(token) ?? [];
      // Relative references open in place when the host can show them; others stay readable text.
      const open = href && !/^https?:/.test(href) ? openLink?.(href) : undefined;
      out.push(/^https?:/.test(href ?? '') ? <a key={key++} href={href} target="_blank" rel="noreferrer">{label}</a>
        : open ? <a key={key++} href={href} onClick={event => { event.preventDefault(); open(); }}>{label}</a>
        : <span key={key++} title={href}>{label}</span>);
    } else out.push(<em key={key++}>{token.slice(1, -1)}</em>);
    last = match.index + token.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

export function Markdown({ source, imageBase, openLink }: { source: string; imageBase?: string; openLink?: LinkOpener }) {
  const blocks: ReactNode[] = [];
  const lines = source.replace(/\r\n/g, '\n').split('\n');
  let i = 0, key = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (line.startsWith('```')) {
      const body: string[] = []; i++;
      while (i < lines.length && !lines[i].startsWith('```')) body.push(lines[i++]);
      i++; blocks.push(<pre key={key++}><code>{body.join('\n')}</code></pre>); continue;
    }
    const heading = /^(#{1,3})\s+(.*)$/.exec(line);
    if (heading) { const Tag = (`h${heading[1].length}`) as 'h1' | 'h2' | 'h3'; blocks.push(<Tag key={key++}>{inline(heading[2], imageBase, openLink)}</Tag>); i++; continue; }
    if (/^\s*([-*]|\d+\.)\s+/.test(line)) {
      const ordered = /^\s*\d+\./.test(line), items: string[] = [];
      const marker = ordered ? /^\s*\d+\.\s+/ : /^\s*[-*]\s+/;
      while (i < lines.length) {
        if (marker.test(lines[i])) { items.push(lines[i++].replace(marker, '')); continue; }
        if (!lines[i].trim()) {
          let next = i + 1; while (next < lines.length && !lines[next].trim()) next++;
          if (next < lines.length && marker.test(lines[next])) { i = next; continue; }
          break;
        }
        if (/^(#{1,3}\s|```|\s*([-*]|\d+\.)\s|\|)/.test(lines[i])) break;
        // Source guides wrap a bullet over several lines; retain one semantic item.
        items[items.length - 1] += ' ' + lines[i++].trim();
      }
      const List = ordered ? 'ol' : 'ul';
      blocks.push(<List key={key++}>{items.map((item, index) => <li key={index}>{inline(item, imageBase, openLink)}</li>)}</List>); continue;
    }
    if (line.startsWith('|')) {
      const rows: string[][] = [];
      while (i < lines.length && lines[i].startsWith('|')) { const cells = lines[i++].split('|').slice(1, -1).map(cell => cell.trim()); if (!cells.every(cell => /^:?-+:?$/.test(cell))) rows.push(cells); }
      blocks.push(<table key={key++} className="wb-table"><tbody>{rows.map((row, r) => <tr key={r}>{row.map((cell, c) => r === 0 ? <th key={c}>{inline(cell, imageBase, openLink)}</th> : <td key={c}>{inline(cell, imageBase, openLink)}</td>)}</tr>)}</tbody></table>); continue;
    }
    if (!line.trim()) { i++; continue; }
    const paragraph: string[] = [];
    while (i < lines.length && lines[i].trim() && !/^(#{1,3}\s|```|\s*([-*]|\d+\.)\s|\|)/.test(lines[i])) paragraph.push(lines[i++]);
    blocks.push(<p key={key++}>{inline(paragraph.join(' '), imageBase, openLink)}</p>);
  }
  return <div className="wb-markdown">{blocks}</div>;
}
