/** 启动/致命错误等整页状态屏。 */
import { escapeHtml, icon } from './dom';

export function mountBootScreen(root: HTMLElement): void {
  root.innerHTML = `
    <div class="boot-screen">
      <div class="boot-screen__glow" aria-hidden="true"></div>
      <div class="boot-screen__inner">
        <div class="brand-mark brand-mark--xl">${icon('camera')}</div>
        <div class="boot-screen__name">PicturePick</div>
        <div class="spinner spinner--lg" aria-hidden="true"></div>
        <p class="boot-screen__hint">正在连接本地选片服务…</p>
      </div>
    </div>
  `;
}

export interface FatalScreenOptions {
  title: string;
  detail: string;
  hint?: string;
  onRetry: () => void;
}

export function mountFatalScreen(root: HTMLElement, options: FatalScreenOptions): void {
  root.innerHTML = `
    <div class="fatal-screen">
      <div class="boot-screen__glow" aria-hidden="true"></div>
      <div class="fatal-card">
        <div class="fatal-card__icon">${icon('alert')}</div>
        <h1 class="fatal-card__title">${escapeHtml(options.title)}</h1>
        <p class="fatal-card__detail">${escapeHtml(options.detail)}</p>
        ${options.hint ? `<pre class="fatal-card__hint">${escapeHtml(options.hint)}</pre>` : ''}
        <button type="button" class="btn btn--primary" id="fatal-retry">${icon('refresh')}<span>重新连接</span></button>
      </div>
    </div>
  `;
  root.querySelector<HTMLButtonElement>('#fatal-retry')?.addEventListener('click', options.onRetry);
}
