/** 轻量 toast 通知（右下角堆叠，自动消失，可手动关闭）。 */
import { escapeHtml, icon } from './dom';

export type ToastKind = 'info' | 'success' | 'warn' | 'error';

let container: HTMLElement | null = null;
const timers = new WeakMap<HTMLElement, number>();

function ensureContainer(): HTMLElement {
  if (container && container.isConnected) return container;
  const node = document.createElement('div');
  node.className = 'toast-stack';
  node.setAttribute('role', 'status');
  node.setAttribute('aria-live', 'polite');
  document.body.appendChild(node);
  container = node;
  return node;
}

function kindIcon(kind: ToastKind): string {
  switch (kind) {
    case 'success':
      return icon('check');
    case 'error':
      return icon('alert');
    case 'warn':
      return icon('alert');
    default:
      return icon('info');
  }
}

export function showToast(message: string, kind: ToastKind = 'info', duration = 3200): void {
  const stack = ensureContainer();
  const node = document.createElement('div');
  node.className = `toast toast--${kind}`;
  node.dataset.testid = 'toast';
  node.innerHTML = `<span class="toast__icon">${kindIcon(kind)}</span><span class="toast__message">${escapeHtml(message)}</span><button type="button" class="toast__close" aria-label="关闭提示">${icon('x')}</button>`;
  stack.appendChild(node);

  const remove = (): void => {
    const timer = timers.get(node);
    if (timer !== undefined) window.clearTimeout(timer);
    timers.delete(node);
    node.classList.add('toast--leaving');
    window.setTimeout(() => node.remove(), 180);
  };

  node.querySelector<HTMLButtonElement>('.toast__close')?.addEventListener('click', remove);
  requestAnimationFrame(() => node.classList.add('toast--in'));
  if (duration > 0) {
    timers.set(node, window.setTimeout(remove, duration));
  }
}

/** 清空所有提示（切换视图时调用，避免旧提示残留）。 */
export function clearToasts(): void {
  if (!container) return;
  container.replaceChildren();
}
