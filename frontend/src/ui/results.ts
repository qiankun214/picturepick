/**
 * 结果页：摘要、按 pickCount 排序的入选列表、JSON 下载（浏览器/桌面双路径）。
 */
import { errorText, getResult, photoThumbUrl, resultDownloadUrl } from '../api';
import { isDesktop, safeSaveJson } from '../desktop';
import { buildResultFileName, formatDateTime } from '../state';
import type { PickResult, PickedPhoto, SessionSummary } from '../types';
import { escapeHtml, icon, qs } from './dom';
import { showToast } from './toast';

export interface ResultsViewOptions {
  session: SessionSummary | null;
  result: PickResult;
  onRestart: () => void;
}

type FilterMode = 'picked' | 'all';

export function mountResultsView(root: HTMLElement, options: ResultsViewOptions): void {
  const { result, session } = options;
  // 契约保证后端已排序，这里再排一次，避免后端实现差异影响展示。
  const photos: PickedPhoto[] = [...result.photos].sort((a, b) => {
    if (b.pickCount !== a.pickCount) return b.pickCount - a.pickCount;
    return a.relativePath.localeCompare(b.relativePath, 'zh-Hans-CN');
  });
  const pickedPhotos = photos.filter((photo) => photo.pickCount > 0);
  const sessionId = session?.id ?? '';
  const coveredRounds = session?.rounds ?? [];
  const rankById = new Map(photos.map((photo, index) => [photo.id, index + 1]));

  let filter: FilterMode = 'picked';
  let keyword = '';
  let thumbsEnabled = true;
  let searchTimer = 0;

  root.innerHTML = `
    <div class="results" data-testid="result-view">
      <header class="results-top">
        <div class="topbar__brand">
          <div class="brand-mark brand-mark--sm">${icon('camera')}</div>
          <span class="topbar__name">PicturePick</span>
          <span class="round-chip">结果</span>
        </div>
        <div class="results-top__actions">
          <button type="button" class="btn btn--ghost" data-testid="restart-button">${icon('refresh')}<span>再选一组照片</span></button>
          <button type="button" class="btn btn--primary" data-testid="result-download">${icon('download')}<span>下载 JSON</span></button>
        </div>
      </header>

      <main class="results-main">
        <section class="results-hero">
          <p class="results-hero__eyebrow">${icon('sparkles')} 选片完成</p>
          <h1 class="results-hero__title">从 ${result.totalPhotos} 张照片中，留下 ${result.pickedPhotoCount} 张</h1>
          <p class="results-hero__meta">
            <span title="${escapeHtml(result.sourceDirectory)}">${escapeHtml(result.sourceDirectory)}</span>
            <span class="dot">·</span>
            <span>${formatDateTime(result.generatedAt)} 生成</span>
            <span class="dot">·</span>
            <span>${result.roundsPlayed} 轮筛选</span>
          </p>

          <div class="summary-grid">
            <div class="summary-card">
              <span class="summary-card__value">${result.totalPhotos}</span>
              <span class="summary-card__label">照片总数</span>
            </div>
            <div class="summary-card">
              <span class="summary-card__value">${result.roundsPlayed}</span>
              <span class="summary-card__label">筛选轮次</span>
            </div>
            <div class="summary-card summary-card--accent">
              <span class="summary-card__value">${result.pickedPhotoCount}</span>
              <span class="summary-card__label">最终保留</span>
            </div>
            <div class="summary-card">
              <span class="summary-card__value">${Math.max(0, result.totalPhotos - result.pickedPhotoCount)}</span>
              <span class="summary-card__label">未入选</span>
            </div>
          </div>

          ${
            coveredRounds.length
              ? `<ol class="round-timeline" aria-label="轮次记录">
                  ${coveredRounds
                    .map(
                      (round) => `
                    <li class="round-timeline__item ${round.index === session?.roundIndex ? 'is-current' : ''}">
                      <span class="round-timeline__index">第 ${round.index} 轮</span>
                      <span class="round-timeline__stat">${round.photoCount} 张</span>
                      <span class="round-timeline__pick">${icon('star-filled')} ${round.pickedCount}</span>
                    </li>`,
                    )
                    .join('')}
                </ol>`
              : ''
          }
        </section>

        <section class="results-toolbar">
          <div class="filter-tabs" role="tablist">
            <button type="button" class="filter-tab is-active" data-filter="picked" role="tab">
              入选照片 <span class="filter-tab__count">${pickedPhotos.length}</span>
            </button>
            <button type="button" class="filter-tab" data-filter="all" role="tab">
              全部照片 <span class="filter-tab__count">${photos.length}</span>
            </button>
          </div>
          <div class="results-toolbar__right">
            <label class="search-field">
              ${icon('search')}
              <input type="search" placeholder="搜索文件名或路径…" data-role="search" />
            </label>
            <label class="toggle-field">
              <input type="checkbox" data-role="thumbs" checked />
              <span>缩略图</span>
            </label>
          </div>
        </section>

        <section class="results-list" data-role="list"></section>
        <p class="results-footer-note">${icon('info')} JSON 结果包含全部 ${photos.length} 张照片的 pickCount 与入选轮次。</p>
      </main>
    </div>
  `;

  const list = qs<HTMLElement>(root, '[data-role="list"]');
  const searchInput = qs<HTMLInputElement>(root, '[data-role="search"]');
  const thumbsInput = qs<HTMLInputElement>(root, '[data-role="thumbs"]');
  const downloadButton = qs<HTMLButtonElement>(root, '[data-testid="result-download"]');

  const renderList = (): void => {
    const filtered = photos.filter((photo) => {
      if (filter === 'picked' && photo.pickCount <= 0) return false;
      if (!keyword) return true;
      const haystack = `${photo.name}\n${photo.relativePath}`.toLowerCase();
      return haystack.includes(keyword);
    });

    if (!filtered.length) {
      list.innerHTML = `
        <div class="results-empty">
          ${icon('image')}
          <p>${filter === 'picked' ? '没有符合筛选条件的入选照片' : '没有符合搜索条件的照片'}</p>
        </div>`;
      return;
    }

    const rows = filtered
      .map((photo) => {
        const isPicked = photo.pickCount > 0;
        const roundChips = photo.pickedInRounds
          .map((round) => `<span class="round-dot" title="第 ${round} 轮保留">R${round}</span>`)
          .join('');
        return `
          <article class="result-row ${isPicked ? 'is-picked' : ''}">
            ${
              thumbsEnabled
                ? `<div class="result-thumb">
                     <img data-thumb="${escapeHtml(sessionId ? photoThumbUrl(sessionId, photo.id) : '')}" alt="" loading="lazy" />
                   </div>`
                : ''
            }
            <div class="result-row__info">
              <div class="result-row__name">
                <span class="result-row__filename" title="${escapeHtml(photo.absolutePath)}">${escapeHtml(photo.name)}</span>
                ${isPicked ? `<span class="pick-pill">${icon('star-filled')} ×${photo.pickCount}</span>` : ''}
              </div>
              <div class="result-row__path">${escapeHtml(photo.relativePath)}</div>
            </div>
            <div class="result-row__rounds">${roundChips}</div>
            <div class="result-row__rank">#${rankById.get(photo.id) ?? '–'}</div>
          </article>`;
      })
      .join('');
    list.innerHTML = rows;
    // 懒加载由浏览器原生 loading="lazy" 负责；这里补一次 src 设置，兼容旧 WebView。
    for (const img of Array.from(list.querySelectorAll<HTMLImageElement>('img[data-thumb]'))) {
      const src = img.dataset.thumb;
      if (!src) continue;
      img.addEventListener('error', () => img.classList.add('is-broken'), { once: true });
      img.src = src;
      img.removeAttribute('data-thumb');
    }
  };

  renderList();

  qs<HTMLButtonElement>(root, '[data-testid="restart-button"]').addEventListener('click', () => {
    options.onRestart();
  });

  for (const tab of Array.from(root.querySelectorAll<HTMLButtonElement>('[data-filter]'))) {
    tab.addEventListener('click', () => {
      filter = tab.dataset.filter === 'all' ? 'all' : 'picked';
      for (const node of Array.from(root.querySelectorAll<HTMLElement>('[data-filter]'))) {
        node.classList.toggle('is-active', node === tab);
      }
      renderList();
    });
  }

  searchInput.addEventListener('input', () => {
    window.clearTimeout(searchTimer);
    searchTimer = window.setTimeout(() => {
      keyword = searchInput.value.trim().toLowerCase();
      renderList();
    }, 120);
  });

  thumbsInput.addEventListener('change', () => {
    thumbsEnabled = thumbsInput.checked;
    renderList();
  });

  downloadButton.addEventListener('click', async () => {
    if (!sessionId) {
      showToast('无法定位当前会话，请重新选片', 'error');
      return;
    }
    downloadButton.disabled = true;
    try {
      if (isDesktop()) {
        // 桌面端：preload 原生另存为。内容取后端当前结果（幂等）。
        const latest = await getResult(sessionId);
        const content = JSON.stringify(latest, null, 2);
        const saved = await safeSaveJson(buildResultFileName(latest.sourceDirectory), content);
        if (saved) {
          showToast(`结果已保存到 ${saved}`, 'success', 6000);
        } else {
          showToast('已取消保存', 'info');
        }
      } else {
        const anchor = document.createElement('a');
        anchor.href = resultDownloadUrl(sessionId);
        anchor.download = buildResultFileName(result.sourceDirectory);
        anchor.rel = 'noopener';
        document.body.appendChild(anchor);
        anchor.click();
        anchor.remove();
        showToast('已开始下载选片结果 JSON', 'success');
      }
    } catch (error) {
      showToast(errorText(error), 'error');
    } finally {
      downloadButton.disabled = false;
    }
  });
}
