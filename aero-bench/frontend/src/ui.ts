/**
 * Small text-only DOM helpers shared by the console views. Every value
 * reaches the document through textContent; no helper ever accepts or
 * produces HTML.
 */

export function element<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  className?: string,
  text?: string,
): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (className !== undefined) {
    node.className = className;
  }
  if (text !== undefined) {
    node.textContent = text;
  }
  return node;
}

export function textBlock(text: string): HTMLElement {
  return element("span", undefined, text);
}

export function append(parent: HTMLElement, ...children: (Node | null | undefined)[]): void {
  parent.append(...children.filter((child): child is Node => child !== null && child !== undefined));
}

export function sectionTitle(title: string): HTMLElement {
  return element("div", "section-title", title);
}

export function pill(text: string, tone?: "ok" | "warn" | "bad" | "muted"): HTMLElement {
  const node = element("span", "pill", text);
  if (tone !== undefined) {
    node.classList.add(`tone-${tone}`);
  }
  return node;
}

export function keyValue(label: string, value: string): HTMLElement {
  const row = element("div", "kv");
  append(row, textBlock(label), element("strong", undefined, value));
  return row;
}

export function statusValue(label: string, state: string, tone: "ok" | "warn" | "bad" | "muted"): HTMLElement {
  const row = element("div", "status-line");
  const value = element("strong", `tone-${tone}`, state);
  append(row, textBlock(label), value);
  return row;
}

export function metricCard(label: string, value: string): HTMLElement {
  const card = element("div", "metric");
  append(card, element("small", undefined, label), element("strong", undefined, value));
  return card;
}

export function actionButton(label: string, onClick: () => void, pressed?: boolean): HTMLButtonElement {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "action-button";
  button.textContent = label;
  button.addEventListener("click", onClick);
  if (pressed !== undefined) {
    button.setAttribute("aria-pressed", String(pressed));
  }
  return button;
}

export function emptyRow(text: string): HTMLElement {
  return element("div", "empty-row", text);
}

export function bar(ratio01: number): HTMLElement {
  const barNode = element("div", "bar");
  const fill = element("div");
  fill.style.width = `${Math.max(0, Math.min(1, ratio01)) * 100}%`;
  barNode.append(fill);
  return barNode;
}
