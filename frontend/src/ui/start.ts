/**
 * 启动页（第 4 版）：选择照片文件夹 + 载入 JSON 恢复现场。
 *
 * - 选择目录：原生对话框只取绝对目录路径，后端扫描并提供图片（契约 §8.1）。
 * - 恢复现场：读取之前保存的结果 JSON → POST /api/sessions/restore →
 *   进入 finished 状态的结果页，可查看/导出/继续筛选（契约 §8.3）。
 */
import { errorText } from '../api';
import { icon, qs } from './dom';
import { showToast } from './toast';

export interface StartViewOptions {
  /** 选择照片目录并创建 server 会话、进入选片；取消/失败由 App 处理提示。 */
  onPickFolder: () => Promise<void>;
  /** 用解析后的结果 JSON 恢复现场（App 负责调用后端并进入结果页）。 */
  onRestore: (payload: Record<string, unknown>) => Promise<void>;
}

function readFileText(file: File): Promise<string> {
  if (typeof file.text === 'function') return file.text();
  return new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result ?? ''));
    reader.onerror = () => reject(reader.error ?? new Error('读取 JSON 文件失败'));
    reader.readAsText(file);
  });
}

export function mountStartView(root: HTMLElement, options: StartViewOptions): void {
  root.innerHTML = `
    <div class="start-screen" data-testid="start-view">
      <div class="ambient" aria-hidden="true"></div>
      <main class="start-card start-card--hero">
        <header class="start-head">
          <div class="brand-mark brand-mark--lg">${icon('camera')}</div>
          <h1 class="start-title">PicturePick</h1>
          <p class="start-tagline">逐轮筛选，留下真正心动的那几张</p>
        </header>

        <div class="start-picker">
          <button type="button" class="pick-folder-button" data-testid="pick-folder-button">
            <span class="pick-folder-button__icon">${icon('folder')}</span>
            <span class="pick-folder-button__text">
              <strong class="pick-folder-button__title">选择照片文件夹</strong>
              <small class="pick-folder-button__hint">弹窗只返回目录路径，照片由本机服务读取</small>
            </span>
            <span class="pick-folder-button__arrow">${icon('arrow-right')}</span>
          </button>

          <div class="restore-row">
            <button type="button" class="btn btn--ghost restore-button" data-testid="restore-button">
              ${icon('upload')}<span class="restore-button__label">载入 JSON 恢复现场</span>
            </button>
            <input
              type="file"
              class="visually-hidden-file"
              data-testid="restore-input"
              accept=".json,application/json"
              aria-hidden="true"
              tabindex="-1"
            />
            <p class="restore-hint">载入之前保存的结果 JSON，可查看结果、重新导出，或继续筛选。</p>
          </div>

          <p class="privacy-note">
            ${icon('info')}
            <span>照片由本机后端直接扫描并通过本地服务按需加载，<strong>浏览器不会读取或上传照片</strong>。</span>
          </p>
          <p class="start-status" data-role="status" hidden></p>
          <p class="start-alternative">
            也可以运行 <code>python -m picturepick serve --dir &lt;照片目录&gt;</code> 直接打开选片页。
          </p>
        </div>

        <footer class="start-foot">
          <span class="start-pill">${icon('image')}<span>支持 .jpg / .jpeg</span></span>
          <span class="start-pill">${icon('star')}<span>多轮精选</span></span>
          <span class="start-pill">${icon('upload')}<span>JSON 恢复现场</span></span>
          <span class="start-pill">${icon('download')}<span>一键导出保留照片</span></span>
        </footer>
      </main>
    </div>
  `;

  const pickButton = qs<HTMLButtonElement>(root, '[data-testid="pick-folder-button"]');
  const pickTitle = qs<HTMLElement>(root, '.pick-folder-button__title');
  const pickHint = qs<HTMLElement>(root, '.pick-folder-button__hint');
  const restoreButton = qs<HTMLButtonElement>(root, '[data-testid="restore-button"]');
  const restoreLabel = qs<HTMLElement>(root, '.restore-button__label');
  const restoreInput = qs<HTMLInputElement>(root, '[data-testid="restore-input"]');
  const status = qs<HTMLElement>(root, '[data-role="status"]');

  type BusyKind = 'folder' | 'restore' | null;
  let busy: BusyKind = null;

  const setBusy = (kind: BusyKind): void => {
    busy = kind;
    pickButton.disabled = kind !== null;
    restoreButton.disabled = kind !== null;
    pickButton.classList.toggle('is-busy', kind === 'folder');
    restoreButton.classList.toggle('is-busy', kind === 'restore');
    pickTitle.textContent = kind === 'folder' ? '正在读取照片目录…' : '选择照片文件夹';
    pickHint.textContent =
      kind === 'folder' ? '正在由本机服务扫描目录中的照片' : '弹窗只返回目录路径，照片由本机服务读取';
    restoreLabel.textContent = kind === 'restore' ? '正在恢复现场…' : '载入 JSON 恢复现场';
    status.hidden = kind === null;
    status.textContent =
      kind === 'folder' ? '正在选择目录并扫描照片…' : kind === 'restore' ? '正在读取 JSON 并恢复选片现场…' : '';
  };

  pickButton.addEventListener('click', async () => {
    if (busy) return;
    setBusy('folder');
    try {
      await options.onPickFolder();
    } catch (error) {
      showToast(errorText(error), 'error', 5000);
    } finally {
      // 成功后视图已被替换；若仍在启动页（取消/失败）则恢复按钮。
      if (document.body.contains(pickButton)) setBusy(null);
    }
  });

  restoreButton.addEventListener('click', () => {
    if (busy) return;
    restoreInput.value = '';
    restoreInput.click();
  });

  const restoreFromFile = async (file: File): Promise<void> => {
    setBusy('restore');
    try {
      let text: string;
      try {
        text = await readFileText(file);
      } catch {
        showToast('读取 JSON 文件失败，请重试', 'error', 4600);
        return;
      }
      let payload: unknown;
      try {
        payload = JSON.parse(text);
      } catch {
        showToast('JSON 文件解析失败，请选择 PicturePick 导出的结果文件', 'error', 5200);
        return;
      }
      if (!payload || typeof payload !== 'object' || Array.isArray(payload)) {
        showToast('JSON 结构不正确：顶层应为对象', 'error', 5000);
        return;
      }
      const record = payload as Record<string, unknown>;
      if (record.schemaVersion !== 1) {
        showToast('JSON 版本不受支持（需要 schemaVersion=1）', 'error', 5200);
        return;
      }
      if (!Array.isArray(record.photos)) {
        showToast('JSON 结构不正确：缺少 photos 数组', 'error', 5200);
        return;
      }
      await options.onRestore(record);
    } catch (error) {
      showToast(errorText(error), 'error', 5600);
    } finally {
      if (document.body.contains(restoreButton)) setBusy(null);
    }
  };

  restoreInput.addEventListener('change', () => {
    const file = restoreInput.files?.[0];
    restoreInput.value = '';
    if (file) void restoreFromFile(file);
  });

  pickButton.focus({ preventScroll: true });
}
