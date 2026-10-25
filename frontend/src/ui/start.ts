/**
 * 启动页（第 2 版）：品牌 + 浏览器原生文件夹选择。
 *
 * 按 docs/API.md §8.1：必须使用浏览器自带文件选择框
 * （隐藏的 <input type="file" webkitdirectory multiple>），不得再实现
 * 自定义目录浏览 / 路径输入 / 后端 fs 接口。
 */
import { errorText } from '../api';
import { icon, qs } from './dom';
import { showToast } from './toast';

export interface StartViewOptions {
  /** 隐藏的目录选择 input（由 App 创建并复用，保证结果页重新选择时是同一个元素）。 */
  folderInput: HTMLInputElement;
  /** 打开浏览器文件夹选择框；取消返回 null。 */
  pickFolderFiles: () => Promise<File[] | null>;
  /** 用选中的文件创建 browser 会话并进入选片。 */
  startSession: (files: File[]) => Promise<void>;
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
              <small class="pick-folder-button__hint">使用浏览器自带的文件夹选择框</small>
            </span>
            <span class="pick-folder-button__arrow">${icon('arrow-right')}</span>
          </button>
          <div class="file-input-slot" data-role="file-input-slot"></div>

          <p class="privacy-note">
            ${icon('info')}
            <span>照片只在本地浏览器中读取，<strong>不会上传到服务器</strong>。</span>
          </p>
          <p class="start-status" data-role="status" hidden></p>
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

  const slot = qs<HTMLElement>(root, '[data-role="file-input-slot"]');
  // input 由 App 创建并复用；视图切换时把同一个元素挂到当前页面。
  options.folderInput.classList.add('visually-hidden-file');
  options.folderInput.setAttribute('aria-hidden', 'true');
  slot.appendChild(options.folderInput);

  const pickButton = qs<HTMLButtonElement>(root, '[data-testid="pick-folder-button"]');
  const title = qs<HTMLElement>(root, '.pick-folder-button__title');
  const hint = qs<HTMLElement>(root, '.pick-folder-button__hint');
  const status = qs<HTMLElement>(root, '[data-role="status"]');

  const setBusy = (busy: boolean): void => {
    pickButton.disabled = busy;
    pickButton.classList.toggle('is-busy', busy);
    title.textContent = busy ? '正在读取照片…' : '选择照片文件夹';
    hint.textContent = busy ? '正在读取所选文件夹中的照片' : '使用浏览器自带的文件夹选择框';
    status.hidden = !busy;
    status.textContent = busy ? '正在读取照片并创建选片会话…' : '';
  };

  const chooseFolder = async (): Promise<void> => {
    if (pickButton.disabled) return;
    try {
      const files = await options.pickFolderFiles();
      if (!files || files.length === 0) return; // 用户取消
      setBusy(true);
      await options.startSession(files);
    } catch (error) {
      setBusy(false);
      showToast(errorText(error), 'error', 4600);
      return;
    }
    // 成功后已跳转到选片页；若仍在（异常情况）恢复按钮。
    setBusy(false);
  };

  pickButton.addEventListener('click', () => {
    void chooseFolder();
  });

  // 键盘可访问性：按钮聚焦时 Enter/Space 由原生 button 处理，无需额外绑定。
  const input = options.folderInput;
  input.value = '';
  title.textContent = '选择照片文件夹';
  status.hidden = true;

  // 让启动页在进入时给出明确焦点，便于键盘用户。
  pickButton.focus({ preventScroll: true });

  // 便于一些环境通过拖拽文件夹导入（可选增强，不影响主流程）。
  const screen = qs<HTMLElement>(root, '.start-screen');
  screen.addEventListener('dragover', (event) => {
    event.preventDefault();
    screen.classList.add('is-dragover');
  });
  screen.addEventListener('dragleave', () => screen.classList.remove('is-dragover'));
  screen.addEventListener('drop', (event) => {
    event.preventDefault();
    screen.classList.remove('is-dragover');
    showToast('请点击「选择照片文件夹」，使用浏览器自带的文件夹选择框', 'info', 3200);
  });
}
