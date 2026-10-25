/**
 * 结果页（第 3 版）：
 * - 摘要 / 轮次时间线 / 入选与全部切换 / 搜索 / JSON 下载 / 再选一组（保持原功能）
 * - 「导出保留照片」：目标目录只取绝对路径（桌面原生对话框 → 后端对话框 → 测试注入），
 *   然后调用 POST /api/sessions/{sid}/export-photos，由**后端复制文件**；
 *   浏览器不下载/不上传照片本体。
 */
import * as api from '../api';
import { errorText, getResult, photoThumbUrl, resultDownloadUrl } from '../api';
import { isDesktop, safeSaveJson } from '../desktop';
import { directoryUnavailableMessage, pickDirectoryPath } from '../directory';
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
  const sessionId = session?.id ?? '';
  const origin = session?.origin ?? result.origin ?? 'server';
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
  const lastRoundPhotos = lastRoundIds
    .map((id) => photoById.get(id))
    .filter((photo): photo is PickedPhoto => Boolean(photo));

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
        </div>
        <div class="results-top__actions">
          <button type="button" class="btn btn--ghost" data-testid="restart-button">${icon('refresh')}<span>再选一组照片</span></button>
          <button type="button" class="btn btn--ghost" data-testid="export-photos" ${
            lastRoundIds.length && sessionId ? '' : 'disabled'
          }>${icon('folder')}<span>导出保留照片</span></button>
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
            <span class="export-panel__hint">${
              origin === 'browser'
                ? '旧版浏览器导入会话不支持导出，请重新选择本机目录'
                : '导出由本机服务直接复制文件，不经过浏览器下载'
            }</span>
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
        <p class="results-footer-note">${icon('info')} JSON 结果包含全部 ${photos.length} 张照片的 pickCount 与入选轮次；「导出保留照片」把最后一轮的 ${lastRoundIds.length} 张复制到指定文件夹。</p>
      </main>
    </div>
  `;

  const list = qs<HTMLElement>(root, '[data-role="list"]');
  const searchInput = qs<HTMLInputElement>(root, '[data-role="search"]');
  const thumbsInput = qs<HTMLInputElement>(root, '[data-role="thumbs"]');
  const downloadButton = qs<HTMLButtonElement>(root, '[data-testid="result-download"]');
  const exportButton = qs<HTMLButtonElement>(root, '[data-testid="export-photos"]');
  const progressEl = qs<HTMLElement>(root, '[data-testid="export-progress"]');

  const setProgress = (message: string, active = false): void => {
    progressEl.textContent = message;
    progressEl.classList.toggle('is-active', active);
  };

  const setBusy = (busy: boolean): void => {
    exporting = busy;
    exportButton.disabled = busy || !lastRoundIds.length || !sessionId;
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

    list.innerHTML = filtered
      .map((photo) => {
        const isPicked = photo.pickCount > 0;
        const roundChips = photo.pickedInRounds
          .map((round) => `<span class="round-dot" title="第 ${round} 轮保留">R${round}</span>`)
          .join('');
        const thumbUrl = thumbsEnabled && sessionId ? photoThumbUrl(sessionId, photo.id) : '';
        return `
          <article class="result-row ${isPicked ? 'is-picked' : ''}">
            ${
              thumbsEnabled
                ? `<div class="result-thumb ${thumbUrl ? '' : 'is-missing'}">
                     <img data-thumb="${escapeHtml(thumbUrl)}" alt="" loading="lazy" />
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

  /* ------------------------------ 导出保留照片（后端复制） ------------------------------ */

  const handleExport = async (): Promise<void> => {
    if (exporting) return;
    if (!sessionId) {
      showToast('无法定位当前会话，请重新选片', 'error');
      return;
    }
    if (!lastRoundIds.length) {
      setProgress('最后一轮没有保留照片，无需导出');
      showToast('最后一轮没有保留照片，无需导出', 'warn', 3600);
      return;
    }
    if (origin === 'browser') {
      const message = '该结果来自旧版浏览器导入会话，导出不可用；请重新选择本机目录再选片';
      setProgress(message);
      showToast(message, 'warn', 5600);
      return;
    }

    setBusy(true);
    try {
      setProgress('正在选择导出目标文件夹…', true);
      const outcome = await pickDirectoryPath({ testPath: window.__picturepickTestExportDir });
      if (outcome.status === 'cancelled') {
        setProgress('已取消导出');
        showToast('已取消导出', 'info', 2200);
        return;
      }
      if (outcome.status === 'unavailable') {
        const message = directoryUnavailableMessage('export');
        setProgress(message);
        showToast(message, 'warn', 7000);
        return;
      }
      if (outcome.status === 'failed') throw outcome.error;

      const destination = outcome.path;
      setProgress(`正在复制 ${lastRoundIds.length} 张照片到 ${destination}…`, true);
      const response = await api.exportPhotos(sessionId, destination);
      const savedTo = response.destination || destination;
      const count = response.copied;
      setProgress(`已导出 ${count} 张到 ${savedTo}`);
      showToast(`已导出 ${count} 张照片到 ${savedTo}`, 'success', 6000);
      if (lastRoundPhotos.length && count === 0) {
        showToast('后端未复制任何照片，请检查目标目录权限', 'warn', 5000);
      }
    } catch (error) {
      const message = errorText(error);
      setProgress(`导出失败：${message}`);
      showToast(`导出失败：${message}`, 'error', 6000);
    } finally {
      setBusy(false);
    }
  };

  exportButton.addEventListener('click', () => {
    void handleExport();
  });
}
