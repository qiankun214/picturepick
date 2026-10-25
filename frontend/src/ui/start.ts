/**
 * 启动页（第 3 版）：品牌 + 「选择照片文件夹」。
 *
 * 点击后由主进程按契约 §8.1 获取**绝对目录路径**（桌面原生对话框 →
 * 本机后端 /api/dialog/pick-directory → 测试注入），再 POST /api/sessions
 * 由后端扫描目录并供图。浏览器不读取/上传任何照片文件。
 */
import { errorText } from '../api';
import { icon, qs } from './dom';
import { showToast } from './toast';

export interface StartViewOptions {
  /** 选择照片目录并创建 server 会话、进入选片；取消/失败由 App 处理提示。 */
  onPickFolder: () => Promise<void>;
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
          <span class="start-pill">${icon('keyboard')}<span>键盘操作</span></span>
          <span class="start-pill">${icon('download')}<span>一键导出保留照片</span></span>
        </footer>
      </main>
    </div>
  `;

  const pickButton = qs<HTMLButtonElement>(root, '[data-testid="pick-folder-button"]');
  const title = qs<HTMLElement>(root, '.pick-folder-button__title');
  const hint = qs<HTMLElement>(root, '.pick-folder-button__hint');
  const status = qs<HTMLElement>(root, '[data-role="status"]');

  const setBusy = (busy: boolean): void => {
    pickButton.disabled = busy;
    pickButton.classList.toggle('is-busy', busy);
    title.textContent = busy ? '正在读取照片目录…' : '选择照片文件夹';
    hint.textContent = busy
      ? '正在由本机服务扫描目录中的照片'
      : '弹窗只返回目录路径，照片由本机服务读取';
    status.hidden = !busy;
    status.textContent = busy ? '正在选择目录并扫描照片…' : '';
  };

  pickButton.addEventListener('click', async () => {
    if (pickButton.disabled) return;
    setBusy(true);
    try {
      await options.onPickFolder();
    } catch (error) {
      showToast(errorText(error), 'error', 5000);
    } finally {
      // 成功后视图已被替换；若仍在启动页（取消/失败）则恢复按钮。
      if (document.body.contains(pickButton)) setBusy(false);
    }
  });

  pickButton.focus({ preventScroll: true });
}
