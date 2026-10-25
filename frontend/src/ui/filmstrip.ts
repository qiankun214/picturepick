/**
 * 底部胶片条：缩略图懒加载（IntersectionObserver）、点击跳转、pick 徽标、当前项高亮。
 * 缩略图统一走 /image?variant=thumb，避免原图解码开销。
 */
import type { PhotoItem } from '../types';
import { photoThumbUrl } from '../api';

export class Filmstrip {
  private readonly root: HTMLElement;
  private readonly sessionId: string;
  private readonly onSelect: (index: number) => void;
  private readonly observer: IntersectionObserver;
  private photos: PhotoItem[] = [];
  private activeIndex = 0;

  constructor(root: HTMLElement, sessionId: string, onSelect: (index: number) => void) {
    this.root = root;
    this.sessionId = sessionId;
    this.onSelect = onSelect;
    this.root.classList.add('filmstrip');
    this.root.setAttribute('role', 'listbox');
    this.root.setAttribute('aria-label', '照片缩略图');

    this.observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (!entry.isIntersecting) continue;
          const img = entry.target as HTMLImageElement;
          const src = img.dataset.src;
          if (src && !img.src) {
            img.src = src;
            img.removeAttribute('data-src');
          }
          this.observer.unobserve(img);
        }
      },
      { root: this.root, rootMargin: '400px 0px', threshold: 0.01 },
    );

    this.root.addEventListener('click', (event) => {
      const target = (event.target as HTMLElement).closest<HTMLElement>('.film-thumb');
      if (!target) return;
      const index = Number(target.dataset.index);
      if (Number.isInteger(index)) this.onSelect(index);
    });
  }

  setPhotos(photos: PhotoItem[]): void {
    this.photos = photos;
    this.observer.disconnect();
    const fragment = document.createDocumentFragment();
    photos.forEach((photo, index) => {
      const item = document.createElement('button');
      item.type = 'button';
      item.className = 'film-thumb';
      item.dataset.index = String(index);
      item.setAttribute('role', 'option');
      item.title = photo.relativePath;
      item.innerHTML = `
        <img class="film-thumb__img" alt="" data-src="${photoThumbUrl(this.sessionId, photo.id)}" loading="lazy" />
        <span class="film-thumb__star" aria-hidden="true">★</span>
        <span class="film-thumb__index">${index + 1}</span>
      `;
      if (photo.picked) item.classList.add('is-picked');
      if (photo.viewed) item.classList.add('is-viewed');
      fragment.appendChild(item);
      const img = item.querySelector<HTMLImageElement>('img');
      if (img) {
        img.addEventListener('error', () => item.classList.add('is-broken'), { once: true });
        this.observer.observe(img);
      }
    });
    this.root.replaceChildren(fragment);
    this.setActive(this.activeIndex);
  }

  setActive(index: number): void {
    this.activeIndex = index;
    const nodes = this.root.querySelectorAll<HTMLElement>('.film-thumb');
    nodes.forEach((node, nodeIndex) => {
      const active = nodeIndex === index;
      node.classList.toggle('is-active', active);
      node.setAttribute('aria-selected', active ? 'true' : 'false');
    });
    const active = nodes[index];
    if (active) active.scrollIntoView({ block: 'nearest', inline: 'center' });
  }

  /** 照片 pick/viewed 状态变化后刷新单个缩略图（不重建 DOM）。 */
  refresh(photo: PhotoItem, index: number): void {
    this.photos[index] = photo;
    const node = this.root.querySelectorAll<HTMLElement>('.film-thumb')[index];
    if (!node) return;
    node.classList.toggle('is-picked', photo.picked);
    node.classList.toggle('is-viewed', photo.viewed);
  }

  destroy(): void {
    this.observer.disconnect();
    this.root.replaceChildren();
  }
}

/** 结束本轮弹窗里展示 pick 缩略图用的 url 辅助（与胶片条一致）。 */
export function thumbUrlFor(sessionId: string, photoId: string): string {
  return photoThumbUrl(sessionId, photoId);
}
