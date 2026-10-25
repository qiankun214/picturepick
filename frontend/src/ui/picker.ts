/**
 * 选片视图：大图浏览、缩放拖拽、自动放映、pick/取消、结束本轮弹窗、切换下一轮。
 *
 * 数据全部以 docs/API.md 为准：
 * - 照片列表 GET /sessions/{sid}/rounds/{i}/photos（自动翻页）
 * - viewed/pick 上报后端，并用返回的 SessionSummary 刷新统计
 * - 进入下一轮 POST rounds/next；结束 POST finish
 */
import * as api from '../api';
import { ApiError, errorText } from '../api';
import type { PhotoLibrary } from '../library';
import { formatBytes, loadPreferences, setIntervalSeconds } from '../state';
import type { PhotoItem, PickResult, ResolveImageUrl, SessionSummary } from '../types';
import { ImageViewer } from '../viewer';
import { escapeHtml, icon, qs, qsa } from './dom';
import { Filmstrip } from './filmstrip';
import { isModalOpen, openModal } from './modal';
import { showToast } from './toast';

export interface PickerViewOptions {
  session: SessionSummary;
  /** 浏览器来源会话的本地文件图库；server 会话也传入（reconcile 无副作用）。 */
  library: PhotoLibrary;
  /** 统一图片地址解析：browser → object URL，server → /image 接口。 */
  resolveImageUrl: ResolveImageUrl;
  onFinished: (session: SessionSummary, result: PickResult) => void;
}

interface PickerDom {
  stage: HTMLElement;
  viewerHost: HTMLElement;
  overlay: HTMLElement;
  meta: HTMLElement;
  pickBadge: HTMLElement;
  pickButton: HTMLButtonElement;
  autoplayButton: HTMLButtonElement;
  intervalInput: HTMLInputElement;
  zoomLabel: HTMLElement;
  statIndex: HTMLElement;
  statViewed: HTMLElement;
  statPicked: HTMLElement;
  statTotal: HTMLElement;
  roundChip: HTMLElement;
  rootPath: HTMLElement;
  progress: HTMLElement;
  filmstripRoot: HTMLElement;
  fullscreenButton: HTMLButtonElement;
}

const MIN_INTERVAL = 1;
const MAX_INTERVAL = 120;

function clampIndex(value: number, max: number): number {
  return Math.max(0, Math.min(max, value));
}

export class PickerView {
  private readonly root: HTMLElement;
  private readonly options: PickerViewOptions;
  private session: SessionSummary;
  private photos: PhotoItem[] = [];
  private index = 0;
  private viewer: ImageViewer | null = null;
  private filmstrip: Filmstrip | null = null;
  private dom!: PickerDom;

  private playing = false;
  private autoplayTimer = 0;
  private intervalSeconds: number;
  private imageSize: { width: number; height: number } | null = null;
  private readonly reportedViewed = new Set<string>();
  private readonly pickingInFlight = new Set<string>();
  private unsubscribeThumb: (() => void) | null = null;
  private destroyed = false;

  constructor(root: HTMLElement, options: PickerViewOptions) {
    this.root = root;
    this.options = options;
    this.session = options.session;
    this.intervalSeconds = loadPreferences().intervalSeconds;
  }

  async mount(): Promise<void> {
    this.render();
    this.dom = this.collectDom();
    this.viewer = new ImageViewer(this.dom.viewerHost, {
      onLoad: (info) => {
        this.imageSize = info;
        this.renderMeta();
      },
      onZoom: (percent) => {
        if (this.dom) this.dom.zoomLabel.textContent = `${percent}%`;
      },
    });
    this.filmstrip = new Filmstrip(this.dom.filmstripRoot, this.options.resolveImageUrl, (index) => {
      this.showPhoto(index);
    });
    // browser 会话的缩略图是异步生成的，完成后替换胶片条中的图片。
    this.unsubscribeThumb = this.options.library.onThumbReady((photoId, url) => {
      this.filmstrip?.updateThumb(photoId, url);
    });
    this.dom.intervalInput.value = String(this.intervalSeconds);
    this.root.addEventListener('click', this.handleClick);
    this.dom.intervalInput.addEventListener('change', this.handleIntervalChange);
    document.addEventListener('keydown', this.handleKeyDown);
    document.addEventListener('fullscreenchange', this.handleFullscreenChange);
    this.updateAutoplayButton();
    await this.loadRound();
  }

  destroy(): void {
    if (this.destroyed) return;
    this.destroyed = true;
    window.clearTimeout(this.autoplayTimer);
    this.autoplayTimer = 0;
    this.root.removeEventListener('click', this.handleClick);
    this.dom?.intervalInput.removeEventListener('change', this.handleIntervalChange);
    document.removeEventListener('keydown', this.handleKeyDown);
    document.removeEventListener('fullscreenchange', this.handleFullscreenChange);
    this.unsubscribeThumb?.();
    this.unsubscribeThumb = null;
    this.viewer?.destroy();
    this.filmstrip?.destroy();
    this.viewer = null;
    this.filmstrip = null;
  }

  /* ------------------------------ 渲染 ------------------------------ */

  private render(): void {
    const session = this.session;
    this.root.innerHTML = `
      <div class="picker" data-testid="picker-view">
        <header class="topbar">
          <div class="topbar__brand">
            <div class="brand-mark brand-mark--sm">${icon('camera')}</div>
            <span class="topbar__name">PicturePick</span>
            <span class="round-chip" data-role="round-chip">第 ${session.roundIndex} 轮</span>
            ${
              session.origin === 'browser'
                ? `<span class="origin-chip" title="照片只在本地浏览器中读取，不会上传到服务器">${icon('folder')}本地</span>`
                : ''
            }
          </div>
          <div class="topbar__path" data-role="root-path" title="${escapeHtml(session.root)}">${escapeHtml(session.root)}</div>
          <div class="topbar__stats">
            <div class="stat">
              <span class="stat__value" data-role="stat-index" data-testid="stats-index">–</span>
              <span class="stat__label">当前</span>
            </div>
            <div class="stat">
              <span class="stat__value" data-role="stat-viewed" data-testid="stats-viewed">–</span>
              <span class="stat__label">已浏览</span>
            </div>
            <div class="stat stat--accent">
              <span class="stat__value" data-role="stat-picked" data-testid="stats-picked">0</span>
              <span class="stat__label">本轮保留</span>
            </div>
            <div class="stat stat--total">
              <span class="stat__value" data-role="stat-total" data-testid="stats-total">${session.roundPhotoCount}</span>
              <span class="stat__label">本轮总数</span>
            </div>
          </div>
          <div class="topbar__actions">
            <button type="button" class="btn btn--ghost" data-action="fullscreen" data-testid="fullscreen-button" title="全屏 (F)">
              ${icon('maximize')}<span class="btn__label">全屏</span>
            </button>
            <button type="button" class="btn btn--danger" data-action="end-round" data-testid="end-round-button" title="结束本轮并决定下一步">
              ${icon('flag')}<span class="btn__label">结束本轮</span>
            </button>
          </div>
        </header>

        <div class="progress"><div class="progress__fill" data-role="progress"></div></div>

        <main class="stage" data-role="stage">
          <div class="viewer-host" data-role="viewer-host"></div>
          <button type="button" class="stage-nav stage-nav--prev" data-action="prev" data-testid="prev-button" title="上一张 (←)">${icon('chevron-left')}</button>
          <button type="button" class="stage-nav stage-nav--next" data-action="next" data-testid="next-button" title="下一张 (→)">${icon('chevron-right')}</button>
          <div class="stage-meta" data-role="meta"></div>
          <div class="stage-badge" data-role="pick-badge" hidden>${icon('star-filled')}<span>已保留</span></div>
          <div class="stage-overlay" data-role="overlay" hidden></div>
        </main>

        <footer class="dock">
          <div class="dock__row">
            <div class="dock__group dock__group--left">
              <button type="button" class="pick-button" data-action="pick" data-testid="pick-button" title="保留 / 取消保留 (P)">
                <span class="pick-button__icon">${icon('star')}</span>
                <span class="pick-button__text">保留</span>
                <kbd class="pick-button__kbd">P</kbd>
              </button>
              <p class="dock__tip">${
                session.origin === 'browser'
                  ? '照片仅在本地浏览器中读取，不会上传'
                  : '保留的照片将进入下一轮继续筛选'
              }</p>
            </div>

            <div class="dock__group dock__group--center">
              <button type="button" class="icon-btn" data-action="prev" title="上一张 (←)">${icon('chevron-left')}</button>
              <button type="button" class="play-button" data-action="autoplay" data-testid="autoplay-toggle" title="自动放映 (空格)">
                ${icon('play')}<span class="play-button__label">自动放映</span>
              </button>
              <button type="button" class="icon-btn" data-action="next" title="下一张 (→)">${icon('chevron-right')}</button>
              <label class="interval-field" title="自动放映间隔">
                <span>间隔</span>
                <input type="number" min="${MIN_INTERVAL}" max="${MAX_INTERVAL}" step="1" data-role="interval" data-testid="interval-input" inputmode="numeric" />
                <span>秒</span>
              </label>
            </div>

            <div class="dock__group dock__group--right">
              <button type="button" class="icon-btn" data-action="zoom-out" data-testid="zoom-out" title="缩小 (-)">${icon('minus')}</button>
              <span class="zoom-label" data-role="zoom">100%</span>
              <button type="button" class="icon-btn" data-action="zoom-in" data-testid="zoom-in" title="放大 (+)">${icon('plus')}</button>
              <span class="dock__divider" aria-hidden="true"></span>
              <button type="button" class="icon-btn" data-action="fit" data-testid="zoom-fit" title="适应窗口 (0)">${icon('fit')}</button>
              <button type="button" class="icon-btn icon-btn--text" data-action="actual" data-testid="zoom-actual" title="原始大小 1:1 (1)">1:1</button>
              <button type="button" class="icon-btn" data-action="rotate" title="旋转 90° (R)">${icon('rotate')}</button>
            </div>
          </div>
          <div class="filmstrip" data-role="filmstrip" data-testid="filmstrip" aria-label="照片缩略图"></div>
        </footer>
      </div>
    `;
  }

  private collectDom(): PickerDom {
    return {
      stage: qs<HTMLElement>(this.root, '[data-role="stage"]'),
      viewerHost: qs<HTMLElement>(this.root, '[data-role="viewer-host"]'),
      overlay: qs<HTMLElement>(this.root, '[data-role="overlay"]'),
      meta: qs<HTMLElement>(this.root, '[data-role="meta"]'),
      pickBadge: qs<HTMLElement>(this.root, '[data-role="pick-badge"]'),
      pickButton: qs<HTMLButtonElement>(this.root, '[data-testid="pick-button"]'),
      autoplayButton: qs<HTMLButtonElement>(this.root, '[data-testid="autoplay-toggle"]'),
      intervalInput: qs<HTMLInputElement>(this.root, '[data-role="interval"]'),
      zoomLabel: qs<HTMLElement>(this.root, '[data-role="zoom"]'),
      statIndex: qs<HTMLElement>(this.root, '[data-role="stat-index"]'),
      statViewed: qs<HTMLElement>(this.root, '[data-role="stat-viewed"]'),
      statPicked: qs<HTMLElement>(this.root, '[data-role="stat-picked"]'),
      statTotal: qs<HTMLElement>(this.root, '[data-role="stat-total"]'),
      roundChip: qs<HTMLElement>(this.root, '[data-role="round-chip"]'),
      rootPath: qs<HTMLElement>(this.root, '[data-role="root-path"]'),
      progress: qs<HTMLElement>(this.root, '[data-role="progress"]'),
      filmstripRoot: qs<HTMLElement>(this.root, '[data-role="filmstrip"]'),
      fullscreenButton: qs<HTMLButtonElement>(this.root, '[data-testid="fullscreen-button"]'),
    };
  }

  private currentPhoto(): PhotoItem | undefined {
    return this.photos[this.index];
  }

  /* ------------------------------ 数据加载 ------------------------------ */

  private async loadRound(): Promise<void> {
    this.showOverlayLoading('正在读取照片列表…');
    try {
      const photos = await api.getAllRoundPhotos(this.session.id, this.session.roundIndex);
      if (this.destroyed) return;
      if (!photos.length) {
        this.showOverlayError('本轮没有可展示的照片', () => void this.loadRound());
        return;
      }
      this.photos = photos;
      this.index = 0;
      this.imageSize = null;
      this.reportedViewed.clear();
      // browser 会话：按 relativePath 把本地 File 绑定到后端返回的 photoId。
      this.options.library.reconcile(photos);
      this.filmstrip?.setPhotos(photos);
      this.hideOverlay();
      this.updateChrome();
      this.showPhoto(0, true);
      showToast(`已加载 ${photos.length} 张照片 · 第 ${this.session.roundIndex} 轮`, 'info', 2200);
    } catch (error) {
      if (this.destroyed) return;
      this.showOverlayError(errorText(error), () => void this.loadRound());
    }
  }

  private showPhoto(index: number, force = false): void {
    if (this.destroyed || !this.photos.length) return;
    const next = clampIndex(index, this.photos.length - 1);
    if (!force && next === this.index && this.imageSize) return;
    this.index = next;
    const photo = this.photos[next];
    if (!photo) return;
    this.imageSize = null;
    this.viewer?.setPhoto(photo.id, this.options.resolveImageUrl);
    this.filmstrip?.setActive(next);
    this.updateChrome();
    this.scheduleAutoplay();
    void this.reportViewed(photo);
  }

  private async reportViewed(photo: PhotoItem): Promise<void> {
    if (photo.viewed || this.reportedViewed.has(photo.id)) return;
    this.reportedViewed.add(photo.id);
    try {
      const summary = await api.markViewed(this.session.id, photo.id);
      if (this.destroyed) return;
      photo.viewed = true;
      this.session = summary;
      const photoIndex = this.photos.indexOf(photo);
      if (photoIndex >= 0) this.filmstrip?.refresh(photo, photoIndex);
      this.updateChrome();
    } catch {
      // 浏览上报失败不打断选片；下次切回该照片会重试。
      this.reportedViewed.delete(photo.id);
    }
  }

  /* ------------------------------ 统计与界面同步 ------------------------------ */

  private updateChrome(): void {
    if (!this.dom || this.destroyed) return;
    const photos = this.photos;
    const total = photos.length;
    const viewed = photos.reduce((count, photo) => count + (photo.viewed ? 1 : 0), 0);
    const picked = photos.reduce((count, photo) => count + (photo.picked ? 1 : 0), 0);
    const current = total ? this.index + 1 : 0;

    this.dom.statIndex.textContent = `${current}/${total}`;
    this.dom.statViewed.textContent = `${viewed}/${total}`;
    this.dom.statPicked.textContent = String(picked);
    this.dom.statTotal.textContent = String(total);
    this.dom.roundChip.textContent = `第 ${this.session.roundIndex} 轮`;
    this.dom.rootPath.textContent = this.session.root;
    this.dom.rootPath.title = this.session.root;
    this.dom.progress.style.width = total ? `${(current / total) * 100}%` : '0%';

    const photo = this.currentPhoto();
    const atStart = this.index <= 0;
    const atEnd = this.index >= total - 1;
    for (const button of qsa<HTMLButtonElement>(this.root, '[data-action="prev"]')) {
      button.disabled = atStart || !total;
    }
    for (const button of qsa<HTMLButtonElement>(this.root, '[data-action="next"]')) {
      button.disabled = atEnd || !total;
    }

    const isPicked = Boolean(photo?.picked);
    this.dom.pickButton.classList.toggle('is-picked', isPicked);
    this.dom.pickButton.setAttribute('aria-pressed', isPicked ? 'true' : 'false');
    const pickIcon = this.dom.pickButton.querySelector<HTMLElement>('.pick-button__icon');
    if (pickIcon) pickIcon.innerHTML = icon(isPicked ? 'star-filled' : 'star');
    const pickText = this.dom.pickButton.querySelector<HTMLElement>('.pick-button__text');
    if (pickText) pickText.textContent = isPicked ? '已保留' : '保留';
    this.dom.pickBadge.hidden = !isPicked;

    this.renderMeta();
  }

  private renderMeta(): void {
    if (!this.dom || this.destroyed) return;
    const photo = this.currentPhoto();
    if (!photo) {
      this.dom.meta.innerHTML = '';
      return;
    }
    const dimensions = this.imageSize ? `${this.imageSize.width} × ${this.imageSize.height}` : '—';
    this.dom.meta.innerHTML = `
      <div class="stage-meta__row">
        <span class="stage-meta__name" title="${escapeHtml(photo.relativePath)}">${escapeHtml(photo.name)}</span>
        ${photo.picked ? `<span class="stage-meta__tag">${icon('star-filled')} 本轮保留</span>` : ''}
        ${photo.pickCount > 0 ? `<span class="stage-meta__tag stage-meta__tag--muted">累计保留 ×${photo.pickCount}</span>` : ''}
      </div>
      <div class="stage-meta__sub">${escapeHtml(photo.relativePath)} · ${formatBytes(photo.sizeBytes)} · ${dimensions}</div>
    `;
  }

  private showOverlayLoading(message: string): void {
    if (!this.dom) return;
    this.dom.overlay.hidden = false;
    this.dom.overlay.innerHTML = `<div class="stage-overlay__box"><span class="spinner spinner--lg"></span><p>${escapeHtml(message)}</p></div>`;
  }

  private showOverlayError(message: string, retry: () => void): void {
    if (!this.dom) return;
    this.dom.overlay.hidden = false;
    this.dom.overlay.innerHTML = `
      <div class="stage-overlay__box stage-overlay__box--error">
        ${icon('alert')}
        <p>${escapeHtml(message)}</p>
        <button type="button" class="btn btn--ghost" data-role="stage-retry">${icon('refresh')}<span>重试</span></button>
      </div>`;
    this.dom.overlay.querySelector<HTMLButtonElement>('[data-role="stage-retry"]')?.addEventListener('click', retry);
  }

  private hideOverlay(): void {
    if (!this.dom) return;
    this.dom.overlay.hidden = true;
    this.dom.overlay.innerHTML = '';
  }

  /* ------------------------------ 交互 ------------------------------ */

  private readonly handleClick = (event: MouseEvent): void => {
    const target = (event.target as HTMLElement).closest<HTMLElement>('[data-action]');
    if (!target) return;
    const action = target.dataset.action;
    switch (action) {
      case 'prev':
        this.showPhoto(this.index - 1);
        break;
      case 'next':
        this.showPhoto(this.index + 1);
        break;
      case 'pick':
        void this.togglePick();
        break;
      case 'autoplay':
        this.toggleAutoplay();
        break;
      case 'zoom-in':
        this.viewer?.zoomIn();
        break;
      case 'zoom-out':
        this.viewer?.zoomOut();
        break;
      case 'fit':
        this.viewer?.fit();
        break;
      case 'actual':
        this.viewer?.actualSize();
        break;
      case 'rotate':
        this.viewer?.rotateClockwise();
        break;
      case 'end-round':
        void this.openEndRoundDialog();
        break;
      case 'fullscreen':
        void this.toggleFullscreen();
        break;
      default:
        break;
    }
  };

  private readonly handleIntervalChange = (): void => {
    if (!this.dom) return;
    const parsed = Number(this.dom.intervalInput.value);
    const normalized = Number.isFinite(parsed)
      ? Math.min(MAX_INTERVAL, Math.max(MIN_INTERVAL, Math.round(parsed)))
      : 3;
    this.intervalSeconds = normalized;
    setIntervalSeconds(normalized);
    this.dom.intervalInput.value = String(normalized);
    if (this.playing) this.scheduleAutoplay();
  };

  private readonly handleKeyDown = (event: KeyboardEvent): void => {
    if (this.destroyed || isModalOpen()) return;
    const target = event.target as HTMLElement | null;
    if (target) {
      const tag = target.tagName;
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || target.isContentEditable) return;
    }
    switch (event.key) {
      case 'ArrowLeft':
        event.preventDefault();
        this.showPhoto(this.index - 1);
        break;
      case 'ArrowRight':
        event.preventDefault();
        this.showPhoto(this.index + 1);
        break;
      case ' ':
      case 'Spacebar':
        event.preventDefault();
        this.toggleAutoplay();
        break;
      case 'p':
      case 'P':
        event.preventDefault();
        void this.togglePick();
        break;
      case '+':
      case '=':
        event.preventDefault();
        this.viewer?.zoomIn();
        break;
      case '-':
      case '_':
        event.preventDefault();
        this.viewer?.zoomOut();
        break;
      case '0':
        event.preventDefault();
        this.viewer?.fit();
        break;
      case '1':
        event.preventDefault();
        this.viewer?.actualSize();
        break;
      case 'r':
      case 'R':
        event.preventDefault();
        this.viewer?.rotateClockwise();
        break;
      case 'f':
      case 'F':
        event.preventDefault();
        void this.toggleFullscreen();
        break;
      case 'Escape':
        if (document.fullscreenElement) void document.exitFullscreen();
        break;
      default:
        break;
    }
  };

  private async togglePick(): Promise<void> {
    if (this.destroyed) return;
    const photo = this.currentPhoto();
    if (!photo || this.pickingInFlight.has(photo.id)) return;
    const shouldPick = !photo.picked;
    this.pickingInFlight.add(photo.id);
    this.dom.pickButton.disabled = true;
    try {
      const summary = shouldPick
        ? await api.pickPhoto(this.session.id, photo.id)
        : await api.unpickPhoto(this.session.id, photo.id);
      if (this.destroyed) return;
      photo.picked = shouldPick;
      photo.pickCount = Math.max(0, photo.pickCount + (shouldPick ? 1 : -1));
      this.session = summary;
      const photoIndex = this.photos.indexOf(photo);
      if (photoIndex >= 0) this.filmstrip?.refresh(photo, photoIndex);
      this.updateChrome();
      this.dom.pickButton.classList.remove('is-pulse');
      // 强制重排以重新触发动画
      void this.dom.pickButton.offsetWidth;
      this.dom.pickButton.classList.add('is-pulse');
      showToast(
        shouldPick ? `已保留 · ${photo.name}` : `已取消保留 · ${photo.name}`,
        shouldPick ? 'success' : 'info',
        1600,
      );
    } catch (error) {
      if (error instanceof ApiError && error.code === 'PHASE_FINISHED') {
        showToast('本轮选片已结束，请查看结果', 'warn');
      } else {
        showToast(errorText(error), 'error');
      }
    } finally {
      this.pickingInFlight.delete(photo.id);
      if (!this.destroyed) this.dom.pickButton.disabled = false;
    }
  }

  /* ------------------------------ 自动放映 ------------------------------ */

  private toggleAutoplay(): void {
    if (this.playing) {
      this.stopAutoplay();
      showToast('已暂停自动放映', 'info', 1500);
      return;
    }
    if (this.photos.length <= 1) {
      showToast('只有一张照片，无法自动放映', 'info', 2000);
      return;
    }
    this.playing = true;
    if (this.index >= this.photos.length - 1) {
      this.showPhoto(0);
    }
    this.updateAutoplayButton();
    this.scheduleAutoplay();
    showToast(`自动放映已开始 · 每 ${this.intervalSeconds} 秒一张`, 'info', 1800);
  }

  private stopAutoplay(notice?: string): void {
    this.playing = false;
    window.clearTimeout(this.autoplayTimer);
    this.autoplayTimer = 0;
    if (this.dom) this.updateAutoplayButton();
    if (notice) showToast(notice, 'info', 2200);
  }

  private scheduleAutoplay(): void {
    window.clearTimeout(this.autoplayTimer);
    if (!this.playing) return;
    this.autoplayTimer = window.setTimeout(() => {
      if (this.destroyed || !this.playing) return;
      if (this.index >= this.photos.length - 1) {
        this.stopAutoplay('已到最后一张，自动放映结束');
        return;
      }
      this.showPhoto(this.index + 1);
    }, this.intervalSeconds * 1000);
  }

  private updateAutoplayButton(): void {
    if (!this.dom) return;
    this.dom.autoplayButton.classList.toggle('is-playing', this.playing);
    this.dom.autoplayButton.setAttribute('aria-pressed', this.playing ? 'true' : 'false');
    this.dom.autoplayButton.innerHTML = this.playing
      ? `${icon('pause')}<span class="play-button__label">暂停放映</span>`
      : `${icon('play')}<span class="play-button__label">自动放映</span>`;
  }

  /* ------------------------------ 全屏 ------------------------------ */

  private async toggleFullscreen(): Promise<void> {
    try {
      if (document.fullscreenElement) {
        await document.exitFullscreen();
      } else {
        await document.documentElement.requestFullscreen();
      }
    } catch {
      showToast('当前环境不支持全屏', 'warn');
    }
    this.updateFullscreenButton();
  }

  private readonly handleFullscreenChange = (): void => {
    this.updateFullscreenButton();
  };

  private updateFullscreenButton(): void {
    if (!this.dom) return;
    const isFullscreen = Boolean(document.fullscreenElement);
    this.dom.fullscreenButton.innerHTML = isFullscreen
      ? `${icon('minimize')}<span class="btn__label">退出全屏</span>`
      : `${icon('maximize')}<span class="btn__label">全屏</span>`;
  }

  /* ------------------------------ 结束本轮 / 下一轮 ------------------------------ */

  private async openEndRoundDialog(): Promise<void> {
    if (this.destroyed) return;
    this.stopAutoplay();
    const picked = this.photos.filter((photo) => photo.picked);
    const pickedCount = picked.length;
    const viewedCount = this.photos.reduce((count, photo) => count + (photo.viewed ? 1 : 0), 0);
    const canNextRound = pickedCount >= 2;
    const isLastRound = this.session.rounds.length > 0 && this.session.roundIndex >= this.session.rounds.length;

    // browser 会话：弹窗缩略图使用本地小图 object URL；先确保生成完毕，
    // 避免把全尺寸原图塞进弹窗造成渲染压力。
    if ((this.session.origin ?? 'server') === 'browser' && picked.length) {
      try {
        await this.options.library.ensureThumbs(picked.slice(0, 12).map((photo) => photo.id));
      } catch {
        // 缩略图失败时退化为原图，不影响弹窗。
      }
    }
    if (this.destroyed) return;

    const body = document.createElement('div');
    body.className = 'end-round';
    const thumbs = picked
      .slice(0, 12)
      .map(
        (photo) => `
        <div class="end-round__thumb" title="${escapeHtml(photo.name)}">
          <img src="${this.options.resolveImageUrl(photo.id, 'thumb')}" alt="" loading="lazy" />
        </div>`,
      )
      .join('');
    body.innerHTML = `
      <p class="end-round__lead">本轮共浏览 <strong>${viewedCount}</strong> 张，保留 <strong class="end-round__count">${pickedCount}</strong> 张。</p>
      ${
        thumbs
          ? `<div class="end-round__grid">${thumbs}${picked.length > 12 ? `<span class="end-round__more">+${picked.length - 12}</span>` : ''}</div>`
          : `<div class="end-round__empty">${icon('star')}<span>本轮还没有保留任何照片</span></div>`
      }
      <p class="end-round__hint ${canNextRound ? '' : 'is-warn'}">
        ${
          canNextRound
            ? `${icon('info')} 进入下一轮后，只会从这 ${pickedCount} 张里继续筛选。`
            : `${icon('alert')} 进入下一轮至少需要保留 2 张照片（当前 ${pickedCount} 张），可以继续选片或直接结束。`
        }
      </p>
      ${isLastRound && canNextRound ? `<p class="end-round__note">提示：最后一轮之后仍可继续细分。</p>` : ''}
    `;

    openModal({
      title: '结束本轮',
      subtitle: `第 ${this.session.roundIndex} 轮 · 保留 ${pickedCount} 张 · 已浏览 ${viewedCount} 张`,
      size: 'md',
      testId: 'end-dialog',
      body,
      actions: [
        { label: '继续选片', kind: 'ghost' },
        {
          label: '结束并生成结果',
          kind: 'danger',
          testId: 'finish-button',
          onClick: () => this.finishCurrentSession(),
        },
        {
          label: '进入下一轮',
          kind: 'primary',
          testId: 'next-round-button',
          disabled: !canNextRound,
          onClick: () => this.goToNextRound(),
        },
      ],
    });
  }

  private async finishCurrentSession(): Promise<void> {
    const response = await api.finishSession(this.session.id);
    const finishedSession: SessionSummary = { ...this.session, phase: 'finished' };
    this.stopAutoplay();
    this.destroy();
    this.options.onFinished(finishedSession, response.result);
    showToast(`已生成结果：${response.result.pickedPhotoCount} 张照片入选`, 'success', 3600);
  }

  private async goToNextRound(): Promise<void> {
    const summary = await api.nextRound(this.session.id);
    this.session = summary;
    this.hideOverlay();
    showToast(`进入第 ${summary.roundIndex} 轮 · ${summary.roundPhotoCount} 张照片`, 'success', 2600);
    await this.loadRound();
  }
}
