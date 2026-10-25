/**
 * PicturePick 前端入口与视图路由（第 3 版）。
 *
 * 流程：GET /api/health → GET /api/sessions/current
 *   ├─ server 会话 → 选片 / 结果页（图片全部走后端 /image）
 *   ├─ browser 会话（v2 旧数据）→ 结果页仅可看 JSON；picking 时回启动页并提示重选
 *   └─ 无会话 → 启动页：原生目录对话框只取绝对路径，后端扫描目录并提供图片
 *
 * 浏览器不读取、不遍历、不上传照片文件。
 */
import './styles.css';
import {
  ApiError,
  createSession,
  errorText,
  getCurrentSession,
  getResult,
  health,
  restoreSession,
} from './api';
import { directoryUnavailableMessage, pickDirectoryPath } from './directory';
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
      if (origin === 'browser') {
        // 第 3 版起前端不再读取/上传照片，旧的 browser 会话不能继续选片。
        if (session.phase === 'finished') {
          try {
            const result = await getResult(session.id);
            this.showResults(session, result);
            showToast(
              '该结果来自旧版浏览器导入会话，导出保留照片不可用（仅支持本机目录扫描的会话）',
              'warn',
              6500,
            );
            return;
          } catch (error) {
            showToast(`读取上次结果失败：${errorText(error)}`, 'error', 4800);
            this.showStart();
            return;
          }
        }
        this.showStart();
        showToast('旧版浏览器导入的会话已不再支持，请重新选择照片目录', 'warn', 6000);
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

  /**
   * 选择照片目录：原生对话框只返回绝对路径，随后由后端扫描目录并供图。
   * 路径来源（契约 §8.1）：测试注入 → 桌面宿主原生对话框 → 本机后端对话框。
   */
  private async pickSourceAndStart(): Promise<void> {
    const outcome = await pickDirectoryPath({ testPath: window.__picturepickTestSourceDir });
    switch (outcome.status) {
      case 'picked': {
        const session = await createSession({
          source: 'server',
          directory: outcome.path,
          recursive: true,
        });
        showToast(`已扫描 ${session.libraryTotal} 张照片 · ${session.sourceLabel}`, 'info', 2600);
        this.showPicker(session);
        return;
      }
      case 'cancelled':
        showToast('已取消选择照片文件夹', 'info', 2000);
        return;
      case 'unavailable':
        showToast(directoryUnavailableMessage('source'), 'warn', 7000);
        return;
      case 'failed':
        throw outcome.error;
      default:
        return;
    }
  }

  /**
   * 用结果/状态 JSON 恢复现场：后端重新扫描 sourceDirectory 重建轮次/计数。
   * - ``phase="picking"``（选片页「保存 JSON」中途保存）→ 直接回到选片页继续；
   * - ``phase="finished"``（结束生成或旧 JSON）→ 结果页。
   */
  private async restoreFromJson(payload: Record<string, unknown>): Promise<void> {
    const session = await restoreSession(payload);
    if (session.phase === 'picking') {
      this.showPicker(session);
      showToast(
        `已恢复选片状态：第 ${session.roundIndex} 轮，本轮保留 ${session.pickCount} 张，可继续选片`,
        'success',
        4600,
      );
      return;
    }
    const result = await getResult(session.id);
    this.showResults(session, result);
    showToast(
      `已从 JSON 恢复现场：共 ${result.roundsPlayed} 轮、最终保留 ${result.pickedPhotoCount} 张`,
      'success',
      4200,
    );
  }

  /* ------------------------------ 视图切换 ------------------------------ */

  private showStart(): void {
    this.teardown();
    mountStartView(this.root, {
      onPickFolder: () => this.pickSourceAndStart(),
      onRestore: (payload) => this.restoreFromJson(payload),
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
      onResumed: (resumedSession) => {
        showToast(`进入第 ${resumedSession.roundIndex} 轮继续筛选`, 'success', 3000);
        this.showPicker(resumedSession);
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
        showToast(`读取上次结果失败：${errorText(error)}`, 'error', 4600);
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
