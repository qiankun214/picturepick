/**
 * PicturePick 前端入口与视图路由（第 2 版）。
 *
 * 启动流程：
 *   GET /api/health → GET /api/sessions/current
 *   ├─ server 会话 → 选片 / 结果页（图片走后端 /image 接口）
 *   ├─ browser 会话且本地有文件映射 → 选片 / 结果页（图片走 object URL）
 *   ├─ browser 会话但文件映射丢失（刷新）→ 启动页 + 提示重新选择（picking）
 *   │                                    或结果页（finished，导出时再重新选择）
 *   └─ 无会话 → 启动页（浏览器原生文件夹选择）
 */
import './styles.css';
import { ApiError, createSession, errorText, getCurrentSession, getResult, health, photoImageUrl } from './api';
import { PhotoLibrary, rootFolderName } from './library';
import type { PickResult, ResolveImageUrl, SessionSummary } from './types';
import { PickerView } from './ui/picker';
import { mountResultsView } from './ui/results';
import { mountBootScreen, mountFatalScreen } from './ui/screens';
import { mountStartView } from './ui/start';
import { showToast } from './ui/toast';

const BACKEND_HINT = [
  '请先启动后端服务：',
  '  python -m picturepick serve',
  '',
  '浏览器模式默认端口 8000；桌面端会自动拉起内置后端。',
].join('\n');

class App {
  private readonly root: HTMLElement;
  private readonly library = new PhotoLibrary();
  private readonly folderInput: HTMLInputElement;
  private picker: PickerView | null = null;
  private booting = false;
  private folderPickResolve: ((files: File[] | null) => void) | null = null;
  private folderPickTimer = 0;
  private folderFocusHandler: (() => void) | null = null;

  constructor(root: HTMLElement) {
    this.root = root;
    this.folderInput = this.createFolderInput();
  }

  /* ------------------------------ 浏览器目录选择 ------------------------------ */

  /** 创建整个应用复用的隐藏目录 input（启动页与结果页重新匹配都用它）。 */
  private createFolderInput(): HTMLInputElement {
    const input = document.createElement('input');
    input.type = 'file';
    input.multiple = true;
    input.setAttribute('webkitdirectory', '');
    input.setAttribute('directory', '');
    input.accept = '.jpg,.jpeg,image/jpeg';
    input.dataset.testid = 'folder-input';
    input.className = 'visually-hidden-file';
    input.setAttribute('aria-hidden', 'true');
    input.tabIndex = -1;
    input.addEventListener('change', () => {
      const files = Array.from(input.files ?? []);
      input.value = '';
      if (this.folderPickResolve) {
        this.resolveFolderPick(files.length ? files : null);
        return;
      }
      // 自动化测试（Playwright setInputFiles）会直接触发 change 而不经过
      // 「点击按钮 → input.click()」流程，这里做兜底：启动页直接开始导入。
      if (files.length) this.handleDirectFolderSelection(files);
    });
    document.body.appendChild(input);
    return input;
  }

  /** 打开浏览器文件夹选择框，返回选中的文件（取消返回 null）。 */
  private pickFolderFiles(): Promise<File[] | null> {
    // 若已有一次挂起的选择，先按取消处理，避免 Promise 泄漏。
    if (this.folderPickResolve) this.resolveFolderPick(null);
    return new Promise<File[] | null>((resolve) => {
      this.folderPickResolve = resolve;
      this.folderInput.value = '';
      // 取消检测：对话框关闭后窗口重新获得焦点且没有 change 事件 → 视为取消。
      this.folderFocusHandler = () => {
        window.setTimeout(() => {
          if (this.folderPickResolve === resolve) this.resolveFolderPick(null);
        }, 450);
      };
      window.addEventListener('focus', this.folderFocusHandler);
      this.folderInput.click();
      // 兜底：极端情况下既不触发 change 也不触发 focus 时避免永久挂起。
      this.folderPickTimer = window.setTimeout(() => {
        if (this.folderPickResolve === resolve) this.resolveFolderPick(null);
      }, 5 * 60 * 1000);
    });
  }

  private resolveFolderPick(files: File[] | null): void {
    const resolve = this.folderPickResolve;
    this.folderPickResolve = null;
    if (this.folderPickTimer) {
      window.clearTimeout(this.folderPickTimer);
      this.folderPickTimer = 0;
    }
    if (this.folderFocusHandler) {
      window.removeEventListener('focus', this.folderFocusHandler);
      this.folderFocusHandler = null;
    }
    resolve?.(files);
  }

  /** 没有挂起选择时收到文件（直接 setInputFiles / 其他入口）：启动页直接导入。 */
  private handleDirectFolderSelection(files: File[]): void {
    if (this.picker) return;
    const onStartView = Boolean(document.querySelector('[data-testid="start-view"]'));
    if (!onStartView) return; // 结果页重新匹配文件必须走导出流程的 pickFolderFiles()
    void this.startSessionFromFiles(files).catch((error: unknown) => {
      showToast(errorText(error), 'error', 4600);
    });
  }

  /** 用浏览器选中的 File 创建 browser 来源会话并进入选片。 */
  private async startSessionFromFiles(files: File[]): Promise<void> {
    const prepared = await this.library.loadFiles(files);
    if (!prepared.photos.length) {
      throw new Error('所选文件夹中没有 .jpg / .jpeg 照片，请重新选择');
    }
    const label = rootFolderName(prepared.photos[0].relativePath) || '照片文件夹';
    const session = await createSession({
      source: 'browser',
      label,
      recursive: true,
      photos: prepared.photos,
    });
    this.library.setSession(session.id);
    showToast(`已载入 ${prepared.photos.length} 张照片 · ${label}`, 'info', 2600);
    this.showPicker(session);
  }

  /* ------------------------------ 启动与路由 ------------------------------ */

  async boot(): Promise<void> {
    if (this.booting) return;
    this.booting = true;
    this.teardown();
    mountBootScreen(this.root);
    try {
      await health();
      const current = await getCurrentSession();
      const session = current.session;
      if (!session) {
        this.showStart();
        return;
      }
      const origin = session.origin ?? 'server';
      if (origin === 'browser' && !this.library.hasFiles()) {
        // 浏览器会话的文件只在原页面内存里，刷新后无法恢复。
        if (session.phase === 'finished') {
          try {
            const result = await getResult(session.id);
            this.showResults(session, result);
            showToast('上次浏览器会话的照片文件已丢失；导出保留照片时请重新选择原文件夹', 'warn', 6500);
            return;
          } catch (error) {
            showToast(`读取上次结果失败：${errorText(error)}`, 'error', 4800);
            this.showStart();
            return;
          }
        }
        this.showStart();
        showToast('上次的浏览器会话无法恢复（照片只在本地内存中），请重新选择文件夹', 'warn', 6000);
        return;
      }
      await this.openSession(session);
    } catch (error) {
      const networkIssue = error instanceof ApiError && error.isNetworkError;
      mountFatalScreen(this.root, {
        title: networkIssue ? '无法连接选片服务' : '启动失败',
        detail: errorText(error),
        hint: BACKEND_HINT,
        onRetry: () => {
          void this.boot();
        },
      });
    } finally {
      this.booting = false;
    }
  }

  /* ------------------------------ 视图切换 ------------------------------ */

  private showStart(): void {
    this.teardown();
    this.library.clear();
    mountStartView(this.root, {
      folderInput: this.folderInput,
      pickFolderFiles: () => this.pickFolderFiles(),
      startSession: (files) => this.startSessionFromFiles(files),
    });
  }

  private showPicker(session: SessionSummary): void {
    this.teardown();
    const view = new PickerView(this.root, {
      session,
      library: this.library,
      resolveImageUrl: this.imageResolver(session),
      onFinished: (finishedSession, result) => {
        this.showResults(finishedSession, result);
      },
    });
    this.picker = view;
    void view.mount();
  }

  private showResults(session: SessionSummary, result: PickResult): void {
    this.teardown();
    mountResultsView(this.root, {
      session,
      result,
      library: this.library,
      resolveImageUrl: this.imageResolver(session),
      folderInput: this.folderInput,
      pickFolderFiles: () => this.pickFolderFiles(),
      onRestart: () => {
        this.showStart();
      },
    });
  }

  private async openSession(session: SessionSummary): Promise<void> {
    if (session.phase === 'finished') {
      try {
        const result = await getResult(session.id);
        this.showResults(session, result);
        return;
      } catch (error) {
        // 结果读取失败时退回启动页，避免卡在空白页。
        showToast(`读取上次结果失败：${errorText(error)}`, 'error', 4600);
        this.showStart();
        return;
      }
    }
    this.showPicker(session);
  }

  /** 统一图片 URL 解析：browser 用本地 object URL，server 用后端图片接口。 */
  private imageResolver(session: SessionSummary): ResolveImageUrl {
    if ((session.origin ?? 'server') === 'browser') {
      return (photoId, variant) =>
        variant === 'thumb' ? this.library.getThumbUrl(photoId) : this.library.getUrl(photoId);
    }
    return (photoId, variant) => photoImageUrl(session.id, photoId, variant);
  }

  private teardown(): void {
    if (this.picker) {
      this.picker.destroy();
      this.picker = null;
    }
  }
}

const rootElement = document.getElementById('app');
if (!rootElement) {
  throw new Error('页面缺少 #app 挂载点');
}

const app = new App(rootElement);

// 离开页面时提示未结束的选片会话。
window.addEventListener('beforeunload', (event) => {
  if (!document.querySelector('.picker')) return;
  event.preventDefault();
  event.returnValue = '';
});

// 未捕获异常统一提示，避免静默失败。
window.addEventListener('unhandledrejection', (event) => {
  const reason = event.reason;
  if (reason instanceof ApiError && reason.isNetworkError) {
    showToast('与本地服务的连接中断，请确认后端仍在运行', 'error', 5000);
    return;
  }
  showToast(errorText(reason), 'error', 5000);
});

void app.boot();
