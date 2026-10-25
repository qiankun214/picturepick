/**
 * 大图查看器：缩放（以光标为中心）、拖拽平移、旋转、适应窗口、1:1、双击切换。
 * 只处理视口内的图片几何，不关心业务数据；由选片页通过 setPhoto() 传入
 * photoId + resolveImageUrl（browser 为 object URL，server 为 /image 接口）。
 */
import { qs } from './ui/dom';
import type { ImageVariant, ResolveImageUrl } from './types';

export interface ViewerCallbacks {
  onLoad?: (info: { width: number; height: number }) => void;
  onError?: () => void;
  onZoom?: (percent: number) => void;
}

interface DragState {
  pointerId: number;
  startX: number;
  startY: number;
  baseX: number;
  baseY: number;
}

const MIN_SCALE = 0.05;
const MAX_SCALE = 16;

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

export class ImageViewer {
  private readonly viewport: HTMLElement;
  private readonly img: HTMLImageElement;
  private readonly loader: HTMLElement;
  private readonly errorBox: HTMLElement;
  private readonly errorText: HTMLElement;
  private readonly callbacks: ViewerCallbacks;
  private readonly resizeObserver: ResizeObserver;

  private loadToken = 0;
  private currentSrc = '';
  private naturalWidth = 0;
  private naturalHeight = 0;
  private scale = 1;
  private translateX = 0;
  private translateY = 0;
  private rotation = 0;
  private fitScale = 1;
  private mode: 'fit' | 'free' = 'fit';
  private drag: DragState | null = null;
  private destroyed = false;

  constructor(root: HTMLElement, callbacks: ViewerCallbacks = {}) {
    this.callbacks = callbacks;
    root.innerHTML = `
      <div class="viewer">
        <div class="viewer__viewport">
          <img class="viewer__img" alt="" draggable="false" data-testid="viewer-image" />
          <div class="viewer__loader"><span class="spinner"></span><span>正在加载照片…</span></div>
          <div class="viewer__error" hidden>
            <div class="viewer__error-icon">${iconPlaceholder()}</div>
            <p class="viewer__error-text"></p>
            <button type="button" class="btn btn--ghost" data-role="retry">重试</button>
          </div>
        </div>
      </div>
    `;
    this.viewport = qs<HTMLElement>(root, '.viewer__viewport');
    this.img = qs<HTMLImageElement>(root, '.viewer__img');
    this.loader = qs<HTMLElement>(root, '.viewer__loader');
    this.errorBox = qs<HTMLElement>(root, '.viewer__error');
    this.errorText = qs<HTMLElement>(root, '.viewer__error-text');

    this.img.addEventListener('load', this.handleLoad);
    this.img.addEventListener('error', this.handleImageError);
    this.viewport.addEventListener('wheel', this.handleWheel, { passive: false });
    this.viewport.addEventListener('pointerdown', this.handlePointerDown);
    this.viewport.addEventListener('pointermove', this.handlePointerMove);
    this.viewport.addEventListener('pointerup', this.handlePointerUp);
    this.viewport.addEventListener('pointercancel', this.handlePointerUp);
    this.viewport.addEventListener('dblclick', this.handleDoubleClick);
    this.viewport.addEventListener('dragstart', (event) => event.preventDefault());
    qs<HTMLButtonElement>(root, '[data-role="retry"]').addEventListener('click', () => this.reload());

    this.resizeObserver = new ResizeObserver(() => this.handleResize());
    this.resizeObserver.observe(this.viewport);
  }

  /* ------------------------------ 对外 API ------------------------------ */

  /** 设置当前照片：由调用方提供「photoId → URL」解析器，组件不感知来源。 */
  setPhoto(photoId: string, resolve: ResolveImageUrl, variant: ImageVariant = 'original'): void {
    this.setImage(resolve(photoId, variant));
  }

  setImage(src: string): void {
    this.loadToken += 1;
    this.currentSrc = src;
    this.naturalWidth = 0;
    this.naturalHeight = 0;
    this.rotation = 0;
    this.translateX = 0;
    this.translateY = 0;
    this.mode = 'fit';
    this.img.hidden = true;
    this.img.classList.remove('is-ready');
    delete this.img.dataset.loaded;
    this.errorBox.hidden = true;
    this.loader.hidden = false;
    this.img.src = src;
  }

  reload(): void {
    if (!this.currentSrc) return;
    // object URL / data URL 不能追加查询串，直接重新赋值即可。
    if (this.currentSrc.startsWith('blob:') || this.currentSrc.startsWith('data:')) {
      this.setImage(this.currentSrc);
      return;
    }
    const separator = this.currentSrc.includes('?') ? '&' : '?';
    this.setImage(`${this.currentSrc}${separator}_r=${Date.now()}`);
  }

  showError(message: string): void {
    this.loader.hidden = true;
    this.img.hidden = true;
    this.img.classList.remove('is-ready');
    delete this.img.dataset.loaded;
    this.errorText.textContent = message;
    this.errorBox.hidden = false;
  }

  zoomIn(): void {
    this.zoomAtCenter(1.25);
  }

  zoomOut(): void {
    this.zoomAtCenter(1 / 1.25);
  }

  fit(): void {
    this.updateFitScale();
    this.scale = this.fitScale;
    this.translateX = 0;
    this.translateY = 0;
    this.mode = 'fit';
    this.applyTransform();
    this.emitZoom();
  }

  actualSize(): void {
    this.scale = 1;
    this.translateX = 0;
    this.translateY = 0;
    this.mode = 'free';
    this.applyTransform();
    this.emitZoom();
  }

  rotateClockwise(): void {
    this.rotation = (this.rotation + 90) % 360;
    this.updateFitScale();
    if (this.mode === 'fit') this.scale = this.fitScale;
    this.translateX = 0;
    this.translateY = 0;
    this.applyTransform();
    this.emitZoom();
  }

  get zoomPercent(): number {
    return Math.round(this.scale * 100);
  }

  get isFitMode(): boolean {
    return this.mode === 'fit';
  }

  destroy(): void {
    this.destroyed = true;
    this.resizeObserver.disconnect();
    this.viewport.removeEventListener('wheel', this.handleWheel);
    this.viewport.removeEventListener('pointerdown', this.handlePointerDown);
    this.viewport.removeEventListener('pointermove', this.handlePointerMove);
    this.viewport.removeEventListener('pointerup', this.handlePointerUp);
    this.viewport.removeEventListener('pointercancel', this.handlePointerUp);
    this.viewport.removeEventListener('dblclick', this.handleDoubleClick);
  }

  /* ------------------------------ 内部实现 ------------------------------ */

  private readonly handleLoad = (): void => {
    if (this.destroyed) return;
    this.naturalWidth = this.img.naturalWidth;
    this.naturalHeight = this.img.naturalHeight;
    if (!this.naturalWidth || !this.naturalHeight) {
      this.showError('图片尺寸无效');
      return;
    }
    this.img.style.width = `${this.naturalWidth}px`;
    this.img.style.height = `${this.naturalHeight}px`;
    this.img.hidden = false;
    this.loader.hidden = true;
    this.errorBox.hidden = true;
    this.img.classList.add('is-ready');
    this.img.dataset.loaded = 'true';
    this.updateFitScale();
    this.scale = this.fitScale;
    this.translateX = 0;
    this.translateY = 0;
    this.mode = 'fit';
    this.applyTransform();
    this.emitZoom();
    this.callbacks.onLoad?.({ width: this.naturalWidth, height: this.naturalHeight });
  };

  private readonly handleImageError = (): void => {
    if (this.destroyed || !this.currentSrc) return;
    this.showError('图片加载失败，可能已被移动或删除');
    this.callbacks.onError?.();
  };

  private readonly handleWheel = (event: WheelEvent): void => {
    if (!this.naturalWidth) return;
    event.preventDefault();
    const factor = Math.exp(-event.deltaY * 0.0016);
    this.zoomAt(event.clientX, event.clientY, factor);
  };

  private readonly handlePointerDown = (event: PointerEvent): void => {
    if (event.button !== 0 || !this.naturalWidth) return;
    // 拖拽起手时阻止文本选择/图片拖拽。
    event.preventDefault();
    this.viewport.setPointerCapture(event.pointerId);
    this.drag = {
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      baseX: this.translateX,
      baseY: this.translateY,
    };
    this.viewport.classList.add('is-dragging');
  };

  private readonly handlePointerMove = (event: PointerEvent): void => {
    if (!this.drag || this.drag.pointerId !== event.pointerId) return;
    this.translateX = this.drag.baseX + (event.clientX - this.drag.startX);
    this.translateY = this.drag.baseY + (event.clientY - this.drag.startY);
    this.mode = 'free';
    this.constrainTranslation();
    this.applyTransform();
  };

  private readonly handlePointerUp = (event: PointerEvent): void => {
    if (!this.drag || this.drag.pointerId !== event.pointerId) return;
    if (this.viewport.hasPointerCapture(event.pointerId)) {
      this.viewport.releasePointerCapture(event.pointerId);
    }
    this.drag = null;
    this.viewport.classList.remove('is-dragging');
    this.emitZoom();
  };

  private readonly handleDoubleClick = (event: MouseEvent): void => {
    if (!this.naturalWidth) return;
    const nearlyOneToOne = Math.abs(this.fitScale - 1) < 0.001;
    if (this.scale > this.fitScale * 1.001) {
      this.fit();
      return;
    }
    if (nearlyOneToOne) {
      // 小图时 fit 与 1:1 重合，双击放大到 200% 才有意义。
      this.zoomAt(event.clientX, event.clientY, 2 / this.scale);
      return;
    }
    this.actualSize();
  };

  private readonly handleResize = (): void => {
    if (!this.naturalWidth) return;
    this.updateFitScale();
    if (this.mode === 'fit') this.scale = this.fitScale;
    this.constrainTranslation();
    this.applyTransform();
    this.emitZoom();
  };

  private zoomAtCenter(factor: number): void {
    const rect = this.viewport.getBoundingClientRect();
    this.zoomAt(rect.left + rect.width / 2, rect.top + rect.height / 2, factor);
  }

  /** 以屏幕坐标为锚点缩放，保持锚点下的像素不动。 */
  private zoomAt(clientX: number, clientY: number, factor: number): void {
    if (!this.naturalWidth) return;
    const next = clamp(this.scale * factor, MIN_SCALE, MAX_SCALE);
    if (Math.abs(next - this.scale) < 1e-6) return;
    const rect = this.viewport.getBoundingClientRect();
    const anchorX = clientX - (rect.left + rect.width / 2);
    const anchorY = clientY - (rect.top + rect.height / 2);
    const ratio = next / this.scale;
    this.translateX = anchorX - (anchorX - this.translateX) * ratio;
    this.translateY = anchorY - (anchorY - this.translateY) * ratio;
    this.scale = next;
    this.mode = 'free';
    this.constrainTranslation();
    this.applyTransform();
    this.emitZoom();
  }

  private updateFitScale(): void {
    if (!this.naturalWidth || !this.naturalHeight) {
      this.fitScale = 1;
      return;
    }
    const rect = this.viewport.getBoundingClientRect();
    if (rect.width < 1 || rect.height < 1) {
      this.fitScale = 1;
      return;
    }
    const rotated = this.rotation % 180 !== 0;
    const imageWidth = rotated ? this.naturalHeight : this.naturalWidth;
    const imageHeight = rotated ? this.naturalWidth : this.naturalHeight;
    // 小图不放大：fit 最多 100%，避免糊图。
    this.fitScale = Math.min(rect.width / imageWidth, rect.height / imageHeight, 1);
  }

  /** 平移边界：图片小于视口时居中，大于视口时不允许拖出空白。 */
  private constrainTranslation(): void {
    const rect = this.viewport.getBoundingClientRect();
    const rotated = this.rotation % 180 !== 0;
    const imageWidth = (rotated ? this.naturalHeight : this.naturalWidth) * this.scale;
    const imageHeight = (rotated ? this.naturalWidth : this.naturalHeight) * this.scale;
    const limitX = Math.max(0, (imageWidth - rect.width) / 2);
    const limitY = Math.max(0, (imageHeight - rect.height) / 2);
    this.translateX = clamp(this.translateX, -limitX, limitX);
    this.translateY = clamp(this.translateY, -limitY, limitY);
  }

  private applyTransform(): void {
    // 绝对定位在视口中心 + translate(-50%,-50%) 保证任意尺寸/缩放都严格居中。
    this.img.style.transform = `translate3d(${this.translateX}px, ${this.translateY}px, 0) translate(-50%, -50%) rotate(${this.rotation}deg) scale(${this.scale})`;
    this.viewport.classList.toggle('is-zoomed', this.scale > this.fitScale * 1.001);
  }

  private emitZoom(): void {
    this.callbacks.onZoom?.(this.zoomPercent);
  }
}

/** 占位图标（避免 viewer 依赖业务图标表）。 */
function iconPlaceholder(): string {
  return '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3.5" y="4.5" width="17" height="15" rx="2"/><circle cx="9" cy="9.5" r="1.6"/><path d="m4 17 4.5-4.5 3.5 3.5 3-3 5 5"/></svg>';
}
