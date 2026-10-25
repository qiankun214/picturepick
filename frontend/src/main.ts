/**
 * PicturePick 前端入口与视图路由。
 *
 * 启动流程（docs/ARCHITECTURE.md §4）：
 *   GET /api/health → GET /api/sessions/current
 *   ├─ phase=picking → 选片视图
 *   ├─ phase=finished → 结果页
 *   └─ 无会话 → 启动页（目录选择）
 */
import './styles.css';
import { ApiError, createSession, errorText, getCurrentSession, getResult, health } from './api';
import type { PickResult, SessionSummary } from './types';
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
  private picker: PickerView | null = null;
  private booting = false;

  constructor(root: HTMLElement) {
    this.root = root;
  }

  async boot(): Promise<void> {
    if (this.booting) return;
    this.booting = true;
    this.teardown();
    mountBootScreen(this.root);
    try {
      await health();
      const current = await getCurrentSession();
      if (current.session) {
        await this.openSession(current.session);
      } else {
        this.showStart();
      }
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

  private showStart(initialDirectory?: string): void {
    this.teardown();
    mountStartView(this.root, {
      initialDirectory,
      onStart: async (directory, recursive) => {
        const session = await createSession(directory, recursive);
        this.showPicker(session);
      },
    });
  }

  private showPicker(session: SessionSummary): void {
    this.teardown();
    const view = new PickerView(this.root, {
      session,
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
        showToast(`读取上次结果失败：${errorText(error)}`, 'error', 4200);
        this.showStart();
        return;
      }
    }
    this.showPicker(session);
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
  const session = document.querySelector('.picker');
  if (!session) return;
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
