/**
 * 启动页：目录输入 + 最近目录 + 目录浏览弹窗 + 系统目录选择框（桌面/浏览器双模式）。
 */
import * as api from '../api';
import { ApiError, errorText } from '../api';
import { getDesktopBridge, safePickDirectory } from '../desktop';
import {
  baseName,
  clearRecentDirectories,
  forgetDirectory,
  loadPreferences,
  rememberDirectory,
  savePreferences,
} from '../state';
import type { FsEntry, FsRoot } from '../types';
import { escapeHtml, icon, qs } from './dom';
import { openModal } from './modal';
import { showToast } from './toast';

export interface StartViewOptions {
  initialDirectory?: string;
  onStart: (directory: string, recursive: boolean) => Promise<void>;
}

export function mountStartView(root: HTMLElement, options: StartViewOptions): void {
  const prefs = loadPreferences();
  const initial = options.initialDirectory ?? prefs.recentDirectories[0] ?? '';
  const desktopAvailable = Boolean(getDesktopBridge());

  root.innerHTML = `
    <div class="start-screen" data-testid="start-view">
      <div class="ambient" aria-hidden="true"></div>
      <main class="start-card">
        <header class="start-head">
          <div class="brand-mark brand-mark--lg">${icon('camera')}</div>
          <h1 class="start-title">PicturePick</h1>
          <p class="start-tagline">逐轮筛选，留下真正心动的那几张</p>
        </header>

        <form class="start-form" novalidate>
          <label class="field-label" for="path-input">照片目录</label>
          <div class="path-row">
            <div class="path-field">
              ${icon('folder')}
              <input
                id="path-input"
                class="path-input"
                type="text"
                spellcheck="false"
                autocomplete="off"
                placeholder="例如 E:\\photos\\2024"
                data-testid="path-input"
              />
            </div>
            <button type="button" class="btn btn--ghost" id="browse-button" data-testid="browse-button">
              ${icon('grid')}<span>浏览目录</span>
            </button>
            <button type="button" class="btn btn--ghost" id="dialog-button" data-testid="dialog-button"
              title="${desktopAvailable ? '使用系统目录选择框' : '调用本机系统目录选择框（不可用时自动改为目录浏览）'}">
              ${icon('folder')}<span>系统选择</span>
            </button>
          </div>

          <label class="checkbox-row">
            <input type="checkbox" id="recursive-input" />
            <span>包含子目录中的照片</span>
          </label>

          <div class="recent-block" data-role="recent" hidden>
            <div class="recent-head">
              <span class="field-label">最近目录</span>
              <button type="button" class="link-btn" data-role="clear-recent">清空</button>
            </div>
            <div class="recent-list" data-role="recent-list"></div>
          </div>

          <button type="submit" class="btn btn--primary btn--lg start-submit" data-testid="start-button">
            ${icon('arrow-right')}<span class="start-submit__label">开始选片</span>
          </button>
          <p class="start-note">${icon('info')} 支持 .jpg / .jpeg；选片不会修改、移动或删除原始照片。</p>
        </form>

        <footer class="start-foot">
          <span class="start-pill">${icon('star')}<span>多轮精选</span></span>
          <span class="start-pill">${icon('keyboard')}<span>键盘操作</span></span>
          <span class="start-pill">${icon('download')}<span>JSON 结果</span></span>
        </footer>
      </main>
    </div>
  `;

  const pathInput = qs<HTMLInputElement>(root, '[data-testid="path-input"]');
  const recursiveInput = qs<HTMLInputElement>(root, '#recursive-input');
  const startButton = qs<HTMLButtonElement>(root, '[data-testid="start-button"]');
  const startLabel = qs<HTMLElement>(root, '.start-submit__label');
  const browseButton = qs<HTMLButtonElement>(root, '#browse-button');
  const dialogButton = qs<HTMLButtonElement>(root, '#dialog-button');
  const recentBlock = qs<HTMLElement>(root, '[data-role="recent"]');
  const recentList = qs<HTMLElement>(root, '[data-role="recent-list"]');

  pathInput.value = initial;
  recursiveInput.checked = prefs.recursive;

  const renderRecent = (): void => {
    const directories = loadPreferences().recentDirectories;
    recentBlock.hidden = directories.length === 0;
    recentList.innerHTML = directories
      .map(
        (directory) => `
        <div class="recent-chip">
          <button type="button" class="recent-chip__main" data-path="${escapeHtml(directory)}" title="${escapeHtml(directory)}">
            <span class="recent-chip__icon">${icon('folder')}</span>
            <span class="recent-chip__text">
              <strong>${escapeHtml(baseName(directory))}</strong>
              <small>${escapeHtml(directory)}</small>
            </span>
          </button>
          <button type="button" class="recent-chip__remove" data-remove="${escapeHtml(directory)}" aria-label="移除该目录">${icon('x')}</button>
        </div>`,
      )
      .join('');
  };
  renderRecent();

  recentList.addEventListener('click', (event) => {
    const target = event.target as HTMLElement;
    const remove = target.closest<HTMLElement>('[data-remove]');
    if (remove) {
      forgetDirectory(remove.dataset.remove ?? '');
      renderRecent();
      return;
    }
    const chip = target.closest<HTMLElement>('[data-path]');
    if (chip?.dataset.path) {
      pathInput.value = chip.dataset.path;
      pathInput.focus();
    }
  });

  qs<HTMLButtonElement>(root, '[data-role="clear-recent"]').addEventListener('click', () => {
    clearRecentDirectories();
    renderRecent();
    showToast('已清空最近目录', 'info');
  });

  const setBusy = (busy: boolean, label = '开始选片'): void => {
    startButton.disabled = busy;
    startLabel.textContent = busy ? '正在扫描照片…' : label;
    startButton.classList.toggle('is-busy', busy);
  };

  const start = async (): Promise<void> => {
    const directory = pathInput.value.trim();
    if (!directory) {
      showToast('请先填写或选择照片目录', 'warn');
      pathInput.focus();
      return;
    }
    setBusy(true);
    try {
      savePreferences({ recursive: recursiveInput.checked });
      await options.onStart(directory, recursiveInput.checked);
      rememberDirectory(directory);
    } catch (error) {
      setBusy(false);
      showToast(errorText(error), 'error');
    }
  };

  qs<HTMLFormElement>(root, '.start-form').addEventListener('submit', (event) => {
    event.preventDefault();
    void start();
  });

  pathInput.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') {
      event.preventDefault();
      void start();
    }
  });

  browseButton.addEventListener('click', () => {
    openFolderBrowser(pathInput.value.trim() || undefined, (picked) => {
      pathInput.value = picked;
      pathInput.focus();
    });
  });

  dialogButton.addEventListener('click', async () => {
    dialogButton.disabled = true;
    try {
      if (desktopAvailable) {
        const picked = await safePickDirectory();
        if (picked) {
          pathInput.value = picked;
          pathInput.focus();
        }
        return;
      }
      const result = await api.pickDirectoryDialog();
      if (result.cancelled || !result.path) {
        showToast('已取消目录选择', 'info');
        return;
      }
      pathInput.value = result.path;
      pathInput.focus();
    } catch (error) {
      if (error instanceof ApiError && (error.code === 'DIALOG_UNAVAILABLE' || error.status === 501)) {
        showToast('系统目录选择框不可用，已为你打开目录浏览', 'warn');
        openFolderBrowser(pathInput.value.trim() || undefined, (picked) => {
          pathInput.value = picked;
          pathInput.focus();
        });
      } else {
        showToast(errorText(error), 'error');
      }
    } finally {
      dialogButton.disabled = false;
    }
  });

  pathInput.focus();
}

/* ------------------------------------------------------------------ */
/* 目录浏览器                                                          */
/* ------------------------------------------------------------------ */

interface BrowserState {
  path: string;
  parent: string | null;
  roots: FsRoot[];
}

function openFolderBrowser(initialPath: string | undefined, onConfirm: (path: string) => void): void {
  const body = document.createElement('div');
  body.className = 'folder-browser';
  body.dataset.testid = 'folder-browser';
  body.innerHTML = `
    <div class="folder-browser__bar">
      <button type="button" class="icon-btn" data-role="up" data-testid="folder-up" title="返回上级目录" disabled>${icon('chevron-up')}</button>
      <div class="folder-browser__crumbs" data-role="crumbs"></div>
      <div class="folder-browser__roots" data-role="roots"></div>
    </div>
    <div class="folder-browser__list" data-role="list"></div>
    <p class="folder-browser__status" data-role="status"></p>
  `;

  const state: BrowserState = { path: initialPath ?? '', parent: null, roots: [] };
  const upButton = qs<HTMLButtonElement>(body, '[data-role="up"]');
  const crumbs = qs<HTMLElement>(body, '[data-role="crumbs"]');
  const rootsBox = qs<HTMLElement>(body, '[data-role="roots"]');
  const list = qs<HTMLElement>(body, '[data-role="list"]');
  const status = qs<HTMLElement>(body, '[data-role="status"]');

  const renderCrumbs = (): void => {
    const normalized = state.path.replace(/\//g, '\\');
    const parts = normalized.split('\\').filter(Boolean);
    if (!parts.length) {
      crumbs.innerHTML = `<span class="crumb crumb--muted">${escapeHtml(state.path || '此电脑')}</span>`;
      return;
    }
    let accumulated = '';
    crumbs.innerHTML = parts
      .map((part, index) => {
        accumulated = index === 0 ? `${part}\\` : `${accumulated}${part}\\`;
        const target = index === 0 ? accumulated : accumulated.replace(/\\$/, '');
        const isLast = index === parts.length - 1;
        const node = `<button type="button" class="crumb ${isLast ? 'is-current' : ''}" data-path="${escapeHtml(target)}">${escapeHtml(part)}</button>`;
        return index === 0 ? node : `<span class="crumb-sep">${icon('chevron-right')}</span>${node}`;
      })
      .join('');

    // 点击面包屑跳转
    for (const crumb of Array.from(crumbs.querySelectorAll<HTMLElement>('[data-path]'))) {
      crumb.addEventListener('click', () => {
        const target = crumb.dataset.path;
        if (target) void load(target);
      });
    }
    crumbs.scrollLeft = crumbs.scrollWidth;
  };

  const renderRoots = (): void => {
    rootsBox.innerHTML = state.roots
      .map(
        (drive) =>
          `<button type="button" class="drive-chip ${drive.path.toLowerCase() === state.path.toLowerCase() ? 'is-active' : ''}" data-path="${escapeHtml(drive.path)}" title="${escapeHtml(drive.path)}">${escapeHtml(drive.name)}</button>`,
      )
      .join('');
    for (const chip of Array.from(rootsBox.querySelectorAll<HTMLElement>('[data-path]'))) {
      chip.addEventListener('click', () => {
        const target = chip.dataset.path;
        if (target) void load(target);
      });
    }
  };

  const renderEntries = (entries: FsEntry[]): void => {
    if (!entries.length) {
      list.innerHTML = `<div class="folder-empty">${icon('folder')}<span>这里没有子文件夹</span></div>`;
      return;
    }
    list.innerHTML = entries
      .map(
        (entry) =>
          `<button type="button" class="folder-entry" data-testid="folder-entry" data-path="${escapeHtml(entry.path)}" title="${escapeHtml(entry.path)}">
            <span class="folder-entry__icon">${icon('folder')}</span>
            <span class="folder-entry__name">${escapeHtml(entry.name)}</span>
            <span class="folder-entry__chevron">${icon('chevron-right')}</span>
          </button>`,
      )
      .join('');
    for (const row of Array.from(list.querySelectorAll<HTMLElement>('.folder-entry'))) {
      row.addEventListener('click', () => {
        const target = row.dataset.path;
        if (target) void load(target);
      });
    }
  };

  const showLoading = (): void => {
    list.innerHTML = `<div class="folder-empty"><span class="spinner"></span><span>正在读取目录…</span></div>`;
    status.textContent = '';
  };

  const showError = (message: string, attemptedPath: string): void => {
    list.innerHTML = `
      <div class="folder-empty folder-empty--error">
        ${icon('alert')}
        <span>${escapeHtml(message)}</span>
        <span class="folder-empty__path">${escapeHtml(attemptedPath)}</span>
        <button type="button" class="btn btn--ghost" data-role="retry">${icon('refresh')}<span>重试</span></button>
      </div>`;
    list.querySelector<HTMLButtonElement>('[data-role="retry"]')?.addEventListener('click', () => {
      void load(attemptedPath);
    });
    status.textContent = message;
  };

  const load = async (path: string): Promise<void> => {
    showLoading();
    status.textContent = '正在读取…';
    try {
      const response = await api.listDirectory(path);
      state.path = response.path;
      state.parent = response.parent;
      status.textContent = `${response.entries.length} 个子文件夹`;
      upButton.disabled = !state.parent;
      renderCrumbs();
      renderRoots();
      renderEntries(response.entries);
    } catch (error) {
      const message = errorText(error);
      status.textContent = message;
      upButton.disabled = !state.parent;
      renderCrumbs();
      renderRoots();
      showError(message, path);
      showToast(message, 'error');
    }
  };

  upButton.addEventListener('click', () => {
    if (state.parent) void load(state.parent);
  });

  openModal({
    title: '选择照片目录',
    subtitle: '只显示文件夹；点击进入子文件夹，选择后点击「选择此目录」',
    size: 'lg',
    testId: 'folder-browser-modal',
    body,
    actions: [
      { label: '取消', kind: 'ghost' },
      {
        label: '选择此目录',
        kind: 'primary',
        testId: 'folder-confirm',
        onClick: () => {
          if (!state.path) {
            showToast('请先进入一个文件夹', 'warn');
            return false;
          }
          onConfirm(state.path);
          return true;
        },
      },
    ],
  });

  // 先取盘符列表，再进入起始目录。
  void (async () => {
    try {
      const roots = await api.getFsRoots();
      state.roots = roots.roots;
      renderRoots();
      const startPath = initialPath?.trim() || roots.startPath;
      await load(startPath);
    } catch (error) {
      showToast(errorText(error), 'error');
      showError(errorText(error), initialPath?.trim() || '此电脑');
    }
  })();
}
