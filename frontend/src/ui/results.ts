/**
 * 结果页（第 2 版）：
 * - 摘要 / 轮次时间线 / 入选与全部列表 / 搜索 / JSON 下载 / 再选一组（保持 v1 功能）
 * - 新增「导出保留照片」：按 result.lastRound.pickedPhotoIds 导出最后一轮保留的照片
 *   → 优先 File System Access API 写入文件夹（同名自动 -1/-2）
 *   → 不支持时降级逐张下载
 *   → browser 会话若本地 File 映射丢失（刷新后），引导重新选择原文件夹并按 relativePath 匹配
 */
import * as api from '../api';
import { errorText, getResult, resultDownloadUrl } from '../api';
import { isDesktop, safeSaveJson } from '../desktop';
import {
  exportPhotosAsDownloads,
  exportPhotosToDirectory,
  requestExportDirectoryHandle,
  type ExportPhotoItem,
  type ExportProgressInfo,
} from '../export';
import type { PhotoLibrary } from '../library';
import { buildResultFileName, formatDateTime } from '../state';
import type { PickResult, PickedPhoto, ResolveImageUrl, SessionSummary } from '../types';
import { escapeHtml, icon, qs } from './dom';
import { showToast } from './toast';

export interface ResultsViewOptions {
  session: SessionSummary | null;
  result: PickResult;
  /** 浏览器来源会话的本地文件图库（server 会话为空图库）。 */
  library: PhotoLibrary;
  /** 统一图片地址解析（browser → object URL，server → /image 接口）。 */
  resolveImageUrl: ResolveImageUrl;
  /** App 复用的隐藏目录 input，导出前重新匹配本地文件时使用。 */
  folderInput: HTMLInputElement;
  /** 打开浏览器文件夹选择框；取消返回 null。 */
  pickFolderFiles: () => Promise<File[] | null>;
  onRestart: () => void;
}

type FilterMode = 'picked' | 'all';

export function mountResultsView(root: HTMLElement, options: ResultsViewOptions): void {
  const { result, session } = options;
  const origin = session?.origin ?? result.origin ?? 'server';
  const sessionId = session?.id ?? '';
  // 契约保证后端已排序，这里再排一次，避免后端实现差异影响展示。
  const photos: PickedPhoto[] = [...result.photos].sort((a, b) => {
    if (b.pickCount !== a.pickCount) return b.pickCount - a.pickCount;
    return a.relativePath.localeCompare(b.relativePath, 'zh-Hans-CN');
  });
  const pickedPhotos = photos.filter((photo) => photo.pickCount > 0);
  const coveredRounds = session?.rounds ?? [];
  const rankById = new Map(photos.map((photo, index) => [photo.id, index + 1]));
  const photoById = new Map(photos.map((photo) => [photo.id, photo]));
  const lastRound = result.lastRound;
  const lastRoundIds = Array.isArray(lastRound?.pickedPhotoIds) ? lastRound.pickedPhotoIds : [];
  const exportItems: ExportPhotoItem[] = lastRoundIds
    .map((id) => photoById.get(id))
    .filter((photo): photo is PickedPhoto => Boolean(photo))
    .map((photo) => ({ id: photo.id, name: photo.name, relativePath: photo.relativePath }));

  let filter: FilterMode = 'picked';
  let keyword = '';
  let thumbsEnabled = true;
  let searchTimer = 0;
  let exporting = false;

  root.innerHTML = `
    <div class="results" data-testid="result-view">
      <header class="results-top">
        <div class="topbar__brand">
          <div class="brand-mark brand-mark--sm">${icon('camera')}</div>
          <span class="topbar__name">PicturePick</span>
          <span class="round-chip">结果</span>
          ${origin === 'browser' ? `<span class="origin-chip">${icon('folder')}本地</span>` : ''}
        </div>
        <div class="results-top__actions">
          <button type="button" class="btn btn--ghost" data-testid="restart-button">${icon('refresh')}<span>再选一组照片</span></button>
          <button type="button" class="btn btn--ghost" data-testid="export-photos">${icon('folder')}<span>导出保留照片</span></button>
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

          <div class="export-panel">
            <div class="export-panel__info">
              <span class="export-panel__title">
                ${icon('star-filled')}
                最后一轮保留 <strong>${lastRoundIds.length}</strong> 张
              </span>
              <span class="export-progress" data-testid="export-progress">
                ${
                  lastRoundIds.length
                    ? `最后一轮保留 ${lastRoundIds.length} 张 · 尚未导出`
                    : '最后一轮没有保留照片，无需导出'
                }
              </span>
            </div>
            <div class="file-input-slot" data-role="file-input-slot"></div>
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
        <p class="results-footer-note">${icon('info')} JSON 结果包含全部 ${photos.length} 张照片的 pickCount 与入选轮次；「导出保留照片」保存最后一轮的 ${lastRoundIds.length} 张。</p>
      </main>
    </div>
  `;

  const slot = qs<HTMLElement>(root, '[data-role="file-input-slot"]');
  options.folderInput.classList.add('visually-hidden-file');
  slot.appendChild(options.folderInput);

  const list = qs<HTMLElement>(root, '[data-role="list"]');
  const searchInput = qs<HTMLInputElement>(root, '[data-role="search"]');
  const thumbsInput = qs<HTMLInputElement>(root, '[data-role="thumbs"]');
  const downloadButton = qs<HTMLButtonElement>(root, '[data-testid="result-download"]');
  const exportButton = qs<HTMLButtonElement>(root, '[data-testid="export-photos"]');
  const progressEl = qs<HTMLElement>(root, '[data-testid="export-progress"]');

  if (!exportItems.length) exportButton.disabled = true;

  const setProgress = (message: string, active = false): void => {
    progressEl.textContent = message;
    progressEl.classList.toggle('is-active', active);
  };

  const setBusy = (busy: boolean): void => {
    exporting = busy;
    exportButton.disabled = busy || !exportItems.length;
    exportButton.classList.toggle('is-busy', busy);
    exportButton.innerHTML = busy
      ? `${icon('refresh')}<span>正在导出…</span>`
      : `${icon('folder')}<span>导出保留照片</span>`;
  };

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
        const thumbUrl = thumbsEnabled ? options.resolveImageUrl(photo.id, 'thumb') : '';
        return `
          <article class="result-row ${isPicked ? 'is-picked' : ''}">
            ${
              thumbsEnabled
                ? `<div class="result-thumb ${thumbUrl ? '' : 'is-missing'}">
                     <img data-thumb="${escapeHtml(thumbUrl)}" data-photo-id="${escapeHtml(photo.id)}" alt="" loading="lazy" />
                   </div>`
                : ''
            }
            <div class="result-row__info">
              <div class="result-row__name">
                <span class="result-row__filename" title="${escapeHtml(photo.absolutePath || photo.relativePath)}">${escapeHtml(photo.name)}</span>
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
    for (const img of Array.from(list.querySelectorAll<HTMLImageElement>('img[data-thumb]'))) {
      const src = img.dataset.thumb;
      if (!src) continue;
      img.addEventListener('error', () => img.classList.add('is-broken'), { once: true });
      img.src = src;
      img.removeAttribute('data-thumb');
    }
  };

  renderList();

  // browser 会话的缩略图异步生成：就绪后替换列表中的对应图片。
  const prefetchThumbs = (items: PickedPhoto[]): void => {
    if (origin !== 'browser') return;
    void options.library.ensureThumbs(items.slice(0, 200).map((photo) => photo.id));
  };
  prefetchThumbs(pickedPhotos);
  let unsubscribeThumb: (() => void) | null = null;
  unsubscribeThumb = options.library.onThumbReady((photoId, url) => {
    if (!list.isConnected) {
      unsubscribeThumb?.();
      unsubscribeThumb = null;
      return;
    }
    const img = list.querySelector<HTMLImageElement>(`img[data-photo-id="${photoId}"]`);
    if (img && img.src !== url) {
      img.classList.remove('is-broken');
      img.src = url;
    }
  });

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
      prefetchThumbs(filter === 'all' ? photos : pickedPhotos);
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

  /* ------------------------------ JSON 下载 ------------------------------ */

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

  /* ------------------------------ 导出保留照片 ------------------------------ */

  const ensureBrowserFiles = async (items: ExportPhotoItem[]): Promise<boolean> => {
    let missing = items.filter((item) => !options.library.has(item.id));
    if (!missing.length) return true;
    setProgress(`需要重新选择原照片文件夹以读取 ${missing.length} 张本地照片…`, true);
    showToast('导出前需要重新选择原照片文件夹，照片不会上传', 'warn', 5200);
    const files = await options.pickFolderFiles();
    if (!files || files.length === 0) {
      setProgress('已取消导出');
      return false;
    }
    await options.library.loadFiles(files);
    options.library.reconcile(result.photos.map((photo) => ({ id: photo.id, relativePath: photo.relativePath })));
    renderList();
    missing = items.filter((item) => !options.library.has(item.id));
    if (missing.length) {
      setProgress(`仍有 ${missing.length} 张照片未能匹配，请确认选择的是原文件夹`);
      showToast(`有 ${missing.length} 张照片未匹配到，导出已取消`, 'error', 5200);
      return false;
    }
    setProgress(`已重新读取 ${items.length} 张本地照片，开始导出…`, true);
    return true;
  };

  const getBlob = async (photo: ExportPhotoItem): Promise<Blob> => {
    if (origin === 'browser') {
      const file = options.library.getFile(photo.id);
      if (!file) throw new Error(`本地照片已丢失：${photo.name}`);
      return file;
    }
    return api.fetchPhotoBlob(sessionId, photo.id, 'original');
  };

  const onProgress = (info: ExportProgressInfo): void => {
    setProgress(`正在导出 ${info.done}/${info.total} · ${info.currentName}`, true);
  };

  exportButton.addEventListener('click', async () => {
    if (exporting || !exportItems.length) return;
    if (!sessionId && origin === 'server') {
      showToast('无法定位当前会话，请重新选片', 'error');
      return;
    }
    setBusy(true);
    try {
      if (origin === 'browser') {
        const ready = await ensureBrowserFiles(exportItems);
        if (!ready) return;
      }

      const outcome = await requestExportDirectoryHandle();
      if (outcome.status === 'cancelled') {
        setProgress('已取消导出');
        return;
      }

      if (outcome.status === 'picked') {
        try {
          const written = await exportPhotosToDirectory(outcome.handle, exportItems, getBlob, onProgress);
          setProgress(`已导出 ${written.count} 张照片到「${written.folderName}」`);
          showToast(`已导出 ${written.count} 张照片到「${written.folderName}」`, 'success', 5600);
          return;
        } catch (error) {
          showToast(`写入文件夹失败：${errorText(error)}，已改为逐张下载`, 'warn', 5600);
        }
      } else if (outcome.status === 'unsupported') {
        showToast('当前浏览器不支持直接写入文件夹，已改为逐张下载', 'warn', 5200);
      } else {
        showToast(`无法选择文件夹：${errorText(outcome.error)}，已改为逐张下载`, 'warn', 5600);
      }

      // 降级：逐张下载。
      const downloaded = await exportPhotosAsDownloads(exportItems, getBlob, (info) => {
        setProgress(`正在下载 ${info.done}/${info.total} · ${info.currentName}`, true);
      });
      setProgress(`已开始逐张下载 ${downloaded.count} 张照片，请留意浏览器下载栏`);
      showToast(`已开始逐张下载 ${downloaded.count} 张照片`, 'info', 5200);
    } catch (error) {
      setProgress(`导出失败：${errorText(error)}`);
      showToast(`导出失败：${errorText(error)}`, 'error', 5600);
    } finally {
      setBusy(false);
    }
  });
}
