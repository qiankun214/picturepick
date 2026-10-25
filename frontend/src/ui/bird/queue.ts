/**
 * 选片页右侧「AI 识鸟队列」（docs/BIRDID-CONTRACT.md §8.1 / §8.3）。
 *
 * - 挂载后每 1000ms 轮询 GET /api/sessions/{sid}/bird-tasks；
 * - 页面隐藏（document.hidden）时暂停轮询，重新可见立即刷新一次；
 * - 轮询失败静默降级（保留上一次列表），下一轮自动恢复；
 * - destroy() 停止定时器并 abort 在途请求；
 * - 卡片按任务 id 增量更新（不整块重建），避免每秒闪烁/重新加载缩略图。
 */
import * as api from '../../api';
import type { BirdTask, BirdTaskStage, BirdTaskStatus } from '../../types';
import { escapeHtml, icon, qs } from '../dom';

export interface BirdQueueOptions {
  root: HTMLElement;
  sessionId: string;
  /** 点击任务卡片：打开结果 / 失败 / 进度模态框。 */
  onOpenTask: (task: BirdTask) => void;
}

const POLL_INTERVAL_MS = 1000;

const STATUS_LABELS: Record<BirdTaskStatus, string> = {
  queued: '排队中',
  running: '识别中',
  completed: '待确认',
  adopted: '已采纳',
  dismissed: '已忽略',
  failed: '失败',
};

const STAGE_LABELS: Record<BirdTaskStage, string> = {
  queued: '等待执行',
  preparing: '准备图片',
  identifying: '识别中',
  reference: '获取参考照片',
  done: '已完成',
};

function clampPercent(value: number): number {
  if (!Number.isFinite(value)) return 0;
  return Math.min(100, Math.max(0, value));
}

/** 毫秒 → 可读耗时（1 位小数的秒，超过 60s 用 m s）。 */
export function formatElapsed(ms: number): string {
  if (!Number.isFinite(ms) || ms < 0) return '—';
  if (ms < 1000) return `${Math.round(ms)}ms`;
  const seconds = ms / 1000;
  if (seconds < 60) return `${seconds.toFixed(1)}s`;
  const minutes = Math.floor(seconds / 60);
  return `${minutes}m${Math.round(seconds % 60)}s`;
}

export class BirdQueue {
  private readonly root: HTMLElement;
  private readonly sessionId: string;
  private readonly onOpenTask: (task: BirdTask) => void;

  private listEl!: HTMLElement;
  private emptyEl!: HTMLElement;
  private countEl!: HTMLElement;

  private tasks: BirdTask[] = [];
  private readonly cards = new Map<string, HTMLButtonElement>();
  private timer = 0;
  private polling = false;
  private destroyed = false;
  private inFlight: AbortController | null = null;

  constructor(options: BirdQueueOptions) {
    this.root = options.root;
    this.sessionId = options.sessionId;
    this.onOpenTask = options.onOpenTask;
  }

  mount(): void {
    this.renderSkeleton();
    this.listEl.addEventListener('click', this.handleListClick);
    document.addEventListener('visibilitychange', this.handleVisibility);
    this.startTimer();
    void this.poll();
  }

  destroy(): void {
    if (this.destroyed) return;
    this.destroyed = true;
    this.stopTimer();
    document.removeEventListener('visibilitychange', this.handleVisibility);
    this.listEl.removeEventListener('click', this.handleListClick);
    this.inFlight?.abort();
    this.inFlight = null;
    this.polling = false;
    this.cards.clear();
    this.tasks = [];
    this.root.replaceChildren();
  }

  /* ------------------------------ 查询 ------------------------------ */

  getTasks(): BirdTask[] {
    return this.tasks;
  }

  taskById(taskId: string): BirdTask | undefined {
    return this.tasks.find((task) => task.id === taskId);
  }

  /** 同一照片是否已有排队/识别中的任务（用于「该照片已在识别队列中」提示）。 */
  findActiveByPhoto(photoId: string): BirdTask | undefined {
    return this.tasks.find(
      (task) => task.photoId === photoId && (task.status === 'queued' || task.status === 'running'),
    );
  }

  /* ------------------------------ 轮询 ------------------------------ */

  /** 立即刷新一次（提交/采纳/忽略后调用，无需等下一次轮询）。 */
  refreshNow(): Promise<void> {
    return this.poll();
  }

  /** 本地先用接口返回值更新卡片，随后仍会由下一轮轮询校准。 */
  replaceTask(task: BirdTask): void {
    if (this.destroyed) return;
    const index = this.tasks.findIndex((item) => item.id === task.id);
    const next = this.tasks.slice();
    if (index >= 0) next[index] = task;
    else next.unshift(task);
    this.applyTasks(next);
  }

  private startTimer(): void {
    this.stopTimer();
    this.timer = window.setInterval(() => {
      void this.poll();
    }, POLL_INTERVAL_MS);
  }

  private stopTimer(): void {
    if (this.timer) {
      window.clearInterval(this.timer);
      this.timer = 0;
    }
  }

  private readonly handleVisibility = (): void => {
    if (this.destroyed) return;
    if (document.hidden) {
      this.stopTimer();
      return;
    }
    this.startTimer();
    void this.poll();
  };

  private async poll(): Promise<void> {
    if (this.destroyed || this.polling) return;
    this.polling = true;
    const controller = new AbortController();
    this.inFlight = controller;
    try {
      const response = await api.getBirdTasks(this.sessionId, controller.signal);
      if (this.destroyed) return;
      this.applyTasks(Array.isArray(response.tasks) ? response.tasks : []);
    } catch (error) {
      if (this.destroyed) return;
      if (error instanceof DOMException && error.name === 'AbortError') return;
      // 轮询失败静默降级：保留上一次列表，下一轮自动重试。
    } finally {
      if (this.inFlight === controller) this.inFlight = null;
      this.polling = false;
    }
  }

  /* ------------------------------ 渲染 ------------------------------ */

  private renderSkeleton(): void {
    this.root.innerHTML = `
      <header class="bird-queue__head">
        <span class="bird-queue__title">
          ${icon('bird')}<span>AI 识鸟队列</span>
        </span>
        <span class="bird-queue__count is-idle" data-testid="bird-queue-count" title="共 0 个识鸟任务">0</span>
        <button
          type="button"
          class="icon-btn bird-queue__toggle"
          data-action="toggle-queue"
          data-testid="bird-queue-toggle"
          title="收起 AI 识鸟队列"
          aria-expanded="true"
        >${icon('chevron-right')}</button>
      </header>
      <div class="bird-queue__body">
        <div class="bird-queue__empty" data-testid="bird-queue-empty">
          ${icon('bird')}
          <p class="bird-queue__empty-title">暂无 AI 识鸟任务</p>
          <p class="bird-queue__empty-hint">选中照片后按 <kbd>B</kbd> 或点击顶部「AI 识鸟」提交</p>
        </div>
        <div class="bird-queue__list" data-role="bird-queue-list" hidden></div>
      </div>
    `;
    this.listEl = qs<HTMLElement>(this.root, '[data-role="bird-queue-list"]');
    this.emptyEl = qs<HTMLElement>(this.root, '[data-testid="bird-queue-empty"]');
    this.countEl = qs<HTMLElement>(this.root, '[data-testid="bird-queue-count"]');
  }

  private applyTasks(tasks: BirdTask[]): void {
    this.tasks = tasks;
    const count = tasks.length;
    this.countEl.textContent = String(count);
    this.countEl.title = count ? `共 ${count} 个识鸟任务` : '暂无识鸟任务';
    this.countEl.classList.toggle('is-idle', count === 0);
    this.emptyEl.hidden = count > 0;
    this.listEl.hidden = count === 0;

    const seen = new Set<string>();
    let position = 0;
    for (const task of tasks) {
      seen.add(task.id);
      let card = this.cards.get(task.id);
      if (!card) {
        card = this.createCard(task);
        this.cards.set(task.id, card);
      }
      this.updateCard(card, task);
      // 只在位置不对时才移动节点，避免每秒无谓重排/打断 hover。
      const current = this.listEl.children[position] ?? null;
      if (current !== card) this.listEl.insertBefore(card, current);
      position += 1;
    }
    for (const [taskId, card] of Array.from(this.cards.entries())) {
      if (seen.has(taskId)) continue;
      card.remove();
      this.cards.delete(taskId);
    }
  }

  private createCard(task: BirdTask): HTMLButtonElement {
    const card = document.createElement('button');
    card.type = 'button';
    card.className = 'bird-task';
    card.dataset.testid = 'bird-task-card';
    card.dataset.taskId = task.id;
    card.dataset.status = task.status;

    const thumbnail = String(task.thumbnailUrl ?? '').trim();
    card.innerHTML = `
      <span class="bird-task__thumb${thumbnail ? '' : ' is-missing'}">
        ${thumbnail ? `<img class="bird-task__img" alt="" loading="lazy" src="${escapeHtml(thumbnail)}" />` : ''}
      </span>
      <span class="bird-task__main">
        <span class="bird-task__top">
          <span class="bird-task__name"></span>
          <span class="bird-task__pill" data-role="pill"></span>
        </span>
        <span class="bird-task__bar" data-role="bar" aria-hidden="true"><span data-role="bar-fill"></span></span>
        <span class="bird-task__meta" data-role="meta"></span>
      </span>
    `;

    const img = card.querySelector<HTMLImageElement>('.bird-task__img');
    if (img) {
      img.addEventListener(
        'error',
        () => {
          img.remove();
          card.querySelector<HTMLElement>('.bird-task__thumb')?.classList.add('is-missing');
        },
        { once: true },
      );
    }
    return card;
  }

  private updateCard(card: HTMLButtonElement, task: BirdTask): void {
    card.dataset.taskId = task.id;
    card.dataset.status = task.status;
    card.dataset.stage = task.stage;
    card.title = `${task.photoName} · ${STATUS_LABELS[task.status]}`;

    const name = card.querySelector<HTMLElement>('.bird-task__name');
    if (name) name.textContent = task.photoName;

    const pill = card.querySelector<HTMLElement>('[data-role="pill"]');
    if (pill) {
      pill.className = `bird-task__pill bird-task__pill--${task.status}`;
      pill.textContent = STATUS_LABELS[task.status];
    }

    const fill = card.querySelector<HTMLElement>('[data-role="bar-fill"]');
    if (fill) fill.style.width = `${clampPercent(task.progress)}%`;

    const meta = card.querySelector<HTMLElement>('[data-role="meta"]');
    if (meta) meta.innerHTML = this.metaHtml(task);
  }

  private metaHtml(task: BirdTask): string {
    const elapsed = formatElapsed(task.elapsedMs);
    const percent = Math.round(clampPercent(task.progress));
    switch (task.status) {
      case 'queued': {
        const position = task.queuePosition ? `队列第 ${task.queuePosition} 位` : '等待执行';
        return `<span>${escapeHtml(position)}</span><span class="bird-task__dot">·</span><span>${escapeHtml(STAGE_LABELS[task.stage] ?? '排队中')}</span>`;
      }
      case 'running':
        return `<span class="bird-task__accent">${escapeHtml(STAGE_LABELS[task.stage] ?? '识别中')}</span><span class="bird-task__dot">·</span><span>${percent}%</span><span class="bird-task__dot">·</span><span>已用 ${escapeHtml(elapsed)}</span>`;
      case 'completed':
        return `<span class="bird-task__species">${escapeHtml(task.result?.species || '已识别')}</span><span class="bird-task__dot">·</span><span>耗时 ${escapeHtml(elapsed)} · 待确认</span>`;
      case 'adopted':
        return `<span class="bird-task__species">${escapeHtml(task.result?.species || '已采纳')}</span><span class="bird-task__dot">·</span><span>已采纳</span>`;
      case 'dismissed':
        return `<span>${escapeHtml(task.result?.species || '已忽略')}</span><span class="bird-task__dot">·</span><span>已忽略</span>`;
      case 'failed':
      default: {
        const message = String(task.error?.message ?? '').trim() || '识别失败，可点击查看详情';
        const summary = message.length > 42 ? `${message.slice(0, 42)}…` : message;
        return `<span class="bird-task__error" title="${escapeHtml(message)}">${escapeHtml(summary)}</span>`;
      }
    }
  }

  private readonly handleListClick = (event: MouseEvent): void => {
    const card = (event.target as HTMLElement | null)?.closest<HTMLElement>('.bird-task');
    if (!card) return;
    const taskId = card.dataset.taskId;
    if (!taskId) return;
    const task = this.taskById(taskId);
    if (task) this.onOpenTask(task);
  };
}
