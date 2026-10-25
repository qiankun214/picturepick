/**
 * 通用模态框：遮罩 + 焦点管理 + Esc 关闭 + 动作按钮（支持 async）。
 * 选片页的全局快捷键会通过 isModalOpen() 让位给模态框。
 */
import { escapeHtml, icon, qs } from './dom';
import { showToast } from './toast';

export type ModalActionKind = 'primary' | 'ghost' | 'danger';

export interface ModalAction {
  label: string;
  kind?: ModalActionKind;
  disabled?: boolean;
  /** 供 e2e 定位的稳定测试 id。 */
  testId?: string;
  /** 返回 false 可阻止自动关闭（默认成功后关闭）。 */
  onClick?: () => void | boolean | Promise<void | boolean>;
}

export interface ModalOptions {
  title: string;
  subtitle?: string;
  body?: string | Node;
  actions?: ModalAction[];
  size?: 'sm' | 'md' | 'lg';
  dismissible?: boolean;
  /** 供 e2e 定位的稳定测试 id（挂到遮罩根节点）。 */
  testId?: string;
  onClose?: () => void;
}

export interface ModalHandle {
  element: HTMLElement;
  close: () => void;
}

let openCount = 0;

export function isModalOpen(): boolean {
  return openCount > 0;
}

export function openModal(options: ModalOptions): ModalHandle {
  const dismissible = options.dismissible !== false;
  const backdrop = document.createElement('div');
  backdrop.className = 'modal-backdrop';
  if (options.testId) backdrop.dataset.testid = options.testId;
  backdrop.innerHTML = `
    <div class="modal modal--${options.size ?? 'md'}" role="dialog" aria-modal="true" aria-label="${escapeHtml(options.title)}">
      <header class="modal__head">
        <div class="modal__titles">
          <h2 class="modal__title">${escapeHtml(options.title)}</h2>
          ${options.subtitle ? `<p class="modal__subtitle">${escapeHtml(options.subtitle)}</p>` : ''}
        </div>
        ${dismissible ? `<button type="button" class="icon-btn modal__close" aria-label="关闭">${icon('x')}</button>` : ''}
      </header>
      <div class="modal__body"></div>
      <footer class="modal__foot"></footer>
    </div>
  `;
  const dialog = qs<HTMLDivElement>(backdrop, '.modal');
  const body = qs<HTMLDivElement>(backdrop, '.modal__body');
  if (options.body instanceof Node) body.appendChild(options.body);
  else if (options.body) body.innerHTML = options.body;

  let closed = false;
  const close = (): void => {
    if (closed) return;
    closed = true;
    document.removeEventListener('keydown', onKeyDown, true);
    backdrop.classList.add('modal-backdrop--leaving');
    window.setTimeout(() => {
      backdrop.remove();
      openCount = Math.max(0, openCount - 1);
      options.onClose?.();
    }, 140);
  };

  const onKeyDown = (event: KeyboardEvent): void => {
    if (event.key === 'Escape' && dismissible) {
      event.preventDefault();
      event.stopPropagation();
      close();
    }
  };
  document.addEventListener('keydown', onKeyDown, true);

  const footer = qs<HTMLElement>(backdrop, '.modal__foot');
  for (const action of options.actions ?? []) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = `btn btn--${action.kind ?? 'ghost'}`;
    button.textContent = action.label;
    if (action.testId) button.dataset.testid = action.testId;
    button.disabled = action.disabled === true;
    button.addEventListener('click', async () => {
      if (button.disabled) return;
      if (!action.onClick) {
        close();
        return;
      }
      button.disabled = true;
      const previousText = button.textContent;
      button.textContent = '处理中…';
      try {
        const keepOpen = await action.onClick();
        if (keepOpen === false) return;
        close();
      } catch (error) {
        // 调用方负责 toast；这里只恢复按钮，保持弹窗可重试。
        showToast(error instanceof Error ? error.message : String(error), 'error');
      } finally {
        if (!closed) {
          button.disabled = false;
          button.textContent = previousText;
        }
      }
    });
    footer.appendChild(button);
  }
  if (!footer.childElementCount) footer.remove();

  if (dismissible) {
    backdrop.addEventListener('click', (event) => {
      if (event.target === backdrop) close();
    });
  }

  document.body.appendChild(backdrop);
  openCount += 1;
  requestAnimationFrame(() => backdrop.classList.add('modal-backdrop--in'));

  const focusTarget = qs<HTMLElement>(backdrop, '.modal__foot .btn, .modal__close');
  focusTarget.focus({ preventScroll: true });

  return { element: dialog, close };
}
