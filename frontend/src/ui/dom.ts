/**
 * 无依赖的 DOM 小工具：安全 HTML 转义、查询、图标。
 */

const ESCAPE_MAP: Record<string, string> = {
  '&': '&amp;',
  '<': '&lt;',
  '>': '&gt;',
  '"': '&quot;',
  "'": '&#39;',
};

/** 转义任意值，插入 innerHTML 前必须调用（照片名/路径来自文件系统）。 */
export function escapeHtml(value: unknown): string {
  return String(value ?? '').replace(/[&<>"']/g, (char) => ESCAPE_MAP[char] ?? char);
}

export function qs<T extends Element = Element>(root: ParentNode, selector: string): T {
  const found = root.querySelector<T>(selector);
  if (!found) throw new Error(`元素未找到: ${selector}`);
  return found;
}

export function qsa<T extends Element = Element>(root: ParentNode, selector: string): T[] {
  return Array.from(root.querySelectorAll<T>(selector));
}

export function createElement<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  className?: string,
  text?: string,
): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

/* ------------------------------------------------------------------ */
/* 内联 SVG 图标（无外部 CDN）                                          */
/* ------------------------------------------------------------------ */

const ICON_PATHS = {
  camera:
    '<path d="M4 8.5A1.5 1.5 0 0 1 5.5 7h2l1.2-1.8A1.5 1.5 0 0 1 10 4.5h4a1.5 1.5 0 0 1 1.3.7L16.5 7h2A1.5 1.5 0 0 1 20 8.5v8A1.5 1.5 0 0 1 18.5 18h-13A1.5 1.5 0 0 1 4 16.5z"/><circle cx="12" cy="12.5" r="3.2"/>',
  star: '<path d="m12 3.6 2.6 5.3 5.8.9-4.2 4.1 1 5.8-5.2-2.7-5.2 2.7 1-5.8L3.6 9.8l5.8-.9z"/>',
  'star-filled':
    '<path fill="currentColor" stroke="none" d="m12 3.6 2.6 5.3 5.8.9-4.2 4.1 1 5.8-5.2-2.7-5.2 2.7 1-5.8L3.6 9.8l5.8-.9z"/>',
  'chevron-left': '<path d="m14.5 5.5-6 6.5 6 6.5"/>',
  'chevron-right': '<path d="m9.5 5.5 6 6.5-6 6.5"/>',
  'chevron-up': '<path d="m5.5 14.5 6.5-6 6.5 6"/>',
  'arrow-left': '<path d="M19 12H5"/><path d="m11 6-6 6 6 6"/>',
  'arrow-right': '<path d="M5 12h14"/><path d="m13 6 6 6-6 6"/>',
  folder:
    '<path d="M3.5 7.5A1.5 1.5 0 0 1 5 6h4l2 2.2h8A1.5 1.5 0 0 1 20.5 9.7v7.8A1.5 1.5 0 0 1 19 19H5a1.5 1.5 0 0 1-1.5-1.5z"/>',
  drive:
    '<rect x="3.5" y="5.5" width="17" height="6" rx="1.5"/><rect x="3.5" y="12.5" width="17" height="6" rx="1.5"/><path d="M7 8.5h.01M7 15.5h.01"/>',
  play: '<path fill="currentColor" stroke="none" d="M8 5.2v13.6L19 12z"/>',
  pause: '<path fill="currentColor" stroke="none" d="M7 5h3.4v14H7zM13.6 5H17v14h-3.4z"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  minus: '<path d="M5 12h14"/>',
  fit: '<path d="M4 9V5.5A1.5 1.5 0 0 1 5.5 4H9M15 4h3.5A1.5 1.5 0 0 1 20 5.5V9M20 15v3.5a1.5 1.5 0 0 1-1.5 1.5H15M9 20H5.5A1.5 1.5 0 0 1 4 18.5V15"/>',
  actual: '<path d="M9 8H5v4M15 8h4v4M9 16H5v-4M15 16h4v-4"/>',
  rotate:
    '<path d="M20 11a8 8 0 1 0-2.6 5.9"/><path d="M20 4.5V11h-6.5"/>',
  maximize:
    '<path d="M8 3.5H5.5A2 2 0 0 0 3.5 5.5V8M16 3.5h2.5A2 2 0 0 1 20.5 5.5V8M20.5 16v2.5a2 2 0 0 1-2 2H16M8 20.5H5.5a2 2 0 0 1-2-2V16"/>',
  minimize:
    '<path d="M3.5 8H8V3.5M20.5 8H16V3.5M16 20.5V16h4.5M8 20.5V16H3.5"/>',
  download:
    '<path d="M12 4v11"/><path d="m7.5 10.5 4.5 4.5 4.5-4.5"/><path d="M5 19.5h14"/>',
  upload:
    '<path d="M12 16V5"/><path d="m7.5 9.5 4.5-4.5 4.5 4.5"/><path d="M5 15v3.5A1.5 1.5 0 0 0 6.5 20h11a1.5 1.5 0 0 0 1.5-1.5V15"/>',
  x: '<path d="M6 6l12 12M18 6 6 18"/>',
  check: '<path d="m5 12.5 4.5 4.5L19 7.5"/>',
  refresh:
    '<path d="M20 11.5A8 8 0 1 0 17.7 17"/><path d="M20 5v6.5h-6.5"/>',
  alert:
    '<path d="M12 4.5 21 20H3z"/><path d="M12 10v4.5M12 17.2h.01"/>',
  image:
    '<rect x="3.5" y="4.5" width="17" height="15" rx="2"/><circle cx="9" cy="9.5" r="1.6"/><path d="m4 17 4.5-4.5 3.5 3.5 3-3 5 5"/>',
  keyboard:
    '<rect x="2.5" y="6.5" width="19" height="11" rx="2"/><path d="M6 10h.01M9.5 10h.01M13 10h.01M16.5 10h.01M6 13.5h.01M9.5 13.5h.01M13 13.5h.01M16.5 13.5h.01M8 16.5h8"/>',
  flag: '<path d="M6 20V4"/><path d="M6 5h9.5l-1.8 3.5 1.8 3.5H6"/>',
  clock: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7v5.2l3.2 1.9"/>',
  grid: '<rect x="4" y="4" width="7" height="7" rx="1.5"/><rect x="13" y="4" width="7" height="7" rx="1.5"/><rect x="4" y="13" width="7" height="7" rx="1.5"/><rect x="13" y="13" width="7" height="7" rx="1.5"/>',
  sparkles:
    '<path d="M12 4.5l1.7 3.8 3.8 1.7-3.8 1.7L12 15.5l-1.7-3.8L6.5 10l3.8-1.7z"/><path d="M18.5 15.5l.8 1.7 1.7.8-1.7.8-.8 1.7-.8-1.7-1.7-.8 1.7-.8z"/>',
  info: '<circle cx="12" cy="12" r="8.5"/><path d="M12 11v5M12 7.8h.01"/>',
  external:
    '<path d="M14 4h6v6"/><path d="M20 4 10 14"/><path d="M18 13.5V19a1.5 1.5 0 0 1-1.5 1.5H5A1.5 1.5 0 0 1 3.5 19V7.5A1.5 1.5 0 0 1 5 6h5.5"/>',
  'arrow-up': '<path d="M12 19V5"/><path d="m6 11 6-6 6 6"/>',
  search: '<circle cx="11" cy="11" r="6.5"/><path d="m16 16 4.5 4.5"/>',
  bird: '<path d="M4.8 14.9c0-3.6 2.9-6.5 6.5-6.5h3.9l3.1-2.8c.5-.4 1.3-.1 1.3.6 0 .4-.2.8-.5 1.1l-1.2 1.3a7.2 7.2 0 0 1 2.3 5.2c0 3.6-2.9 6.5-6.5 6.5H8.4l-3.5 2.2 1.1-3.4a6.5 6.5 0 0 1-1.2-4.2z"/><path d="M9.3 11.1h.01"/><path d="M9.6 13.4c1.5-.9 3.1-1.2 4.8-1"/>',
  settings:
    '<circle cx="12" cy="12" r="3"/><path d="M19.1 14.2a1.7 1.7 0 0 0 .4 1.9l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.9-.4 1.7 1.7 0 0 0-1 1.6v.2a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.6 1.7 1.7 0 0 0-1.9.4l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .4-1.9 1.7 1.7 0 0 0-1.6-1H3a2 2 0 1 1 0-4h.2a1.7 1.7 0 0 0 1.6-1.1 1.7 1.7 0 0 0-.4-1.9l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.9.4H9.5a1.7 1.7 0 0 0 1-1.6V3a2 2 0 1 1 4 0v.2a1.7 1.7 0 0 0 1 1.6 1.7 1.7 0 0 0 1.9-.4l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.4 1.9v.1a1.7 1.7 0 0 0 1.6 1h.2a2 2 0 1 1 0 4h-.2a1.7 1.7 0 0 0-1.6 1z"/>',
} as const;

export type IconName = keyof typeof ICON_PATHS;

/** 返回内联 SVG 字符串（currentColor 跟随文字颜色）。 */
export function icon(name: IconName, className = 'icon'): string {
  return `<svg class="${className}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">${ICON_PATHS[name]}</svg>`;
}
