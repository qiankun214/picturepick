/**
 * 目录选择（第 3 版）：浏览器/桌面只获取**绝对目录路径**，照片由后端扫描与供图。
 *
 * 获取顺序：
 *   1. 自动化测试注入（window.__picturepickTestSourceDir / __picturepickTestExportDir）——
 *      契约 §8.1 要求「若存在直接使用，不弹框」；
 *   2. 桌面宿主桥接（Electron preload / pywebview）原生文件夹对话框；
 *   3. 浏览器模式：POST /api/dialog/pick-directory（由本机 Python 后端弹系统对话框）。
 *
 * 全部不可用时返回 unavailable；调用方提示用户改用 `--dir` 或桌面版，
 * 绝不回退自定义目录 UI，也不使用 webkitdirectory / File System Access API。
 */
import { ApiError, pickDirectoryDialog } from './api';
import { getDesktopBridge, safePickDirectory } from './desktop';

declare global {
  interface Window {
    /** 自动化测试注入的「照片源目录」绝对路径。 */
    __picturepickTestSourceDir?: string;
    /** 自动化测试注入的「导出目标目录」绝对路径。 */
    __picturepickTestExportDir?: string;
  }
}

export type DirectoryPickOutcome =
  | { status: 'picked'; path: string; via: 'test' | 'desktop' | 'backend' }
  | { status: 'cancelled' }
  | { status: 'unavailable' }
  | { status: 'failed'; error: unknown };

export interface PickDirectoryOptions {
  /** 测试注入路径（存在时优先使用，不弹任何对话框）。 */
  testPath?: string;
}

function normalizeTestPath(value: string | undefined): string {
  return typeof value === 'string' ? value.trim() : '';
}

/** 获取绝对目录路径。 */
export async function pickDirectoryPath(options: PickDirectoryOptions = {}): Promise<DirectoryPickOutcome> {
  const injected = normalizeTestPath(options.testPath);
  if (injected) return { status: 'picked', path: injected, via: 'test' };

  // 桌面宿主（Electron / pywebview）：原生文件夹对话框。
  if (getDesktopBridge()) {
    const picked = await safePickDirectory();
    if (picked) return { status: 'picked', path: picked, via: 'desktop' };
    // 桌面桥接取消与失败无法区分，按取消处理（用户可再次点击）。
    return { status: 'cancelled' };
  }

  // 浏览器模式：由本机后端弹出系统原生文件夹对话框。
  try {
    const result = await pickDirectoryDialog();
    if (result.cancelled || !result.path) return { status: 'cancelled' };
    return { status: 'picked', path: result.path, via: 'backend' };
  } catch (error) {
    if (error instanceof ApiError && (error.code === 'DIALOG_UNAVAILABLE' || error.status === 501)) {
      return { status: 'unavailable' };
    }
    return { status: 'failed', error };
  }
}

/** 对话框不可用时的统一中文提示（不做自定义目录浏览回退）。 */
export function directoryUnavailableMessage(purpose: 'source' | 'export'): string {
  const action =
    purpose === 'source'
      ? '请改用 `python -m picturepick serve --dir <照片目录>` 或桌面版打开选片'
      : '请在桌面版中导出，或改用命令行/桌面版重新操作';
  return `系统文件夹对话框不可用（DIALOG_UNAVAILABLE）：${action}`;
}
