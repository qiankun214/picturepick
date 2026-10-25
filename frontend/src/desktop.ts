/**
 * 桌面端桥接：同时支持两种桌面外壳
 *
 * 1) Electron preload（contextBridge 注入 window.picturePickDesktop）
 * 2) WebView2 外壳（PyInstaller + pywebview，注入 window.pywebview.api；页面以 ?desktop=webview2 打开）
 *
 * 浏览器模式下两者都不存在，所有能力必须优雅降级：
 * - 目录选择 → 本机后端 /api/dialog/pick-directory（仅取绝对路径，后端扫描照片）
 * - 保存结果 → GET /api/sessions/{sid}/result?download=1 浏览器下载
 * - 打开路径 → 无（仅提示保存路径）
 */

export interface PicturePickDesktopBridge {
  readonly isDesktop?: boolean;
  /** 原生目录选择框，取消时返回 null。 */
  pickDirectory(): Promise<string | null>;
  /** 原生另存为；返回保存的绝对路径，取消时返回 null。 */
  saveJson(defaultName: string, content: string): Promise<string | null>;
  /** 用系统默认程序打开路径。 */
  openPath(path: string): Promise<unknown>;
}

/** pywebview 注入的 Python JS API（方法名为 snake_case）。 */
interface PywebviewApi {
  pick_directory(): Promise<string | null>;
  save_json(defaultName: string, content: string): Promise<string | null>;
  open_path(path: string): Promise<unknown>;
}

declare global {
  interface Window {
    picturePickDesktop?: PicturePickDesktopBridge;
    pywebview?: { api?: PywebviewApi };
  }
}

const WEBVIEW_DESKTOP_HINT = 'webview2';

function isWebviewShell(): boolean {
  try {
    const hint = new URLSearchParams(window.location.search).get('desktop');
    if (hint === WEBVIEW_DESKTOP_HINT) return true;
  } catch {
    /* 忽略异常 URL 场景 */
  }
  return Boolean(window.pywebview);
}

/** pywebview 的 api 对象在页面加载后异步注入，这里轮询等待（最多 3 秒）。 */
async function waitForPywebviewApi(timeoutMs = 3000): Promise<PywebviewApi | null> {
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    const api = window.pywebview?.api;
    if (api && typeof api.pick_directory === 'function') return api;
    if (Date.now() >= deadline) return null;
    await new Promise((resolve) => window.setTimeout(resolve, 50));
  }
}

function createWebviewBridge(): PicturePickDesktopBridge {
  return {
    isDesktop: true,
    async pickDirectory(): Promise<string | null> {
      const api = await waitForPywebviewApi();
      if (!api) return null;
      const picked = await api.pick_directory();
      return typeof picked === 'string' && picked.trim() ? picked : null;
    },
    async saveJson(defaultName: string, content: string): Promise<string | null> {
      const api = await waitForPywebviewApi();
      if (!api) return null;
      const saved = await api.save_json(defaultName, content);
      return typeof saved === 'string' && saved.trim() ? saved : null;
    },
    async openPath(path: string): Promise<unknown> {
      const api = await waitForPywebviewApi();
      if (!api) return false;
      return api.open_path(path);
    },
  };
}

export function getDesktopBridge(): PicturePickDesktopBridge | undefined {
  const bridge = window.picturePickDesktop;
  if (bridge && typeof bridge.pickDirectory === 'function') {
    return bridge;
  }
  if (isWebviewShell()) {
    return createWebviewBridge();
  }
  return undefined;
}

export function isDesktop(): boolean {
  const bridge = getDesktopBridge();
  return Boolean(bridge && bridge.isDesktop !== false);
}

/** 安全调用原生目录选择：异常时返回 null 并让调用方降级。 */
export async function safePickDirectory(): Promise<string | null> {
  const bridge = getDesktopBridge();
  if (!bridge) return null;
  try {
    const path = await bridge.pickDirectory();
    return typeof path === 'string' && path.trim() ? path : null;
  } catch {
    return null;
  }
}

/** 安全调用原生另存为；返回保存路径或 null（取消/失败）。 */
export async function safeSaveJson(defaultName: string, content: string): Promise<string | null> {
  const bridge = getDesktopBridge();
  if (!bridge || typeof bridge.saveJson !== 'function') return null;
  try {
    const saved = await bridge.saveJson(defaultName, content);
    return typeof saved === 'string' && saved.trim() ? saved : null;
  } catch {
    return null;
  }
}

export async function safeOpenPath(path: string): Promise<boolean> {
  const bridge = getDesktopBridge();
  if (!bridge || typeof bridge.openPath !== 'function') return false;
  try {
    await bridge.openPath(path);
    return true;
  } catch {
    return false;
  }
}
