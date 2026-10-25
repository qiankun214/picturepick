/**
 * 一键导出「最后一轮保留的照片」。
 *
 * 首选 File System Access API（window.showDirectoryPicker）写入用户指定文件夹；
 * 自动化测试可注入 window.__picturepickTestExportDirHandle 作为目标目录句柄。
 * 不支持时降级为逐张 <a download> 下载。
 *
 * 这里使用结构化最小接口（DirectoryHandleLike），既兼容真实
 * FileSystemDirectoryHandle，也兼容测试注入的 mock 句柄。
 */
import { fileBaseName } from './library';

export interface ExportPhotoItem {
  id: string;
  name: string;
  relativePath: string;
}

export interface ExportProgressInfo {
  done: number;
  total: number;
  currentName: string;
}

export type GetExportBlob = (photo: ExportPhotoItem) => Promise<Blob>;

interface WritableFileStreamLike {
  write(data: Blob | BufferSource | string): Promise<void>;
  close(): Promise<void>;
}

interface FileHandleLike {
  createWritable(): Promise<WritableFileStreamLike>;
}

/** 与 FileSystemDirectoryHandle 结构兼容的最小子集。 */
export interface DirectoryHandleLike {
  readonly name?: string;
  getFileHandle(name: string, options?: { create?: boolean }): Promise<FileHandleLike>;
  isSameEntry?: (other: unknown) => Promise<boolean>;
}

declare global {
  interface Window {
    showDirectoryPicker?: (options?: {
      mode?: 'read' | 'readwrite';
      id?: string;
    }) => Promise<DirectoryHandleLike>;
    /** e2e 注入的目标目录 mock 句柄；存在时优先于真实选择框。 */
    __picturepickTestExportDirHandle?: DirectoryHandleLike;
  }
}

export type DirectoryPickerOutcome =
  | { status: 'picked'; handle: DirectoryHandleLike }
  | { status: 'cancelled' }
  | { status: 'unsupported' }
  | { status: 'failed'; error: unknown };

/** 是否具备目录导出能力（真实 API 或测试 mock）。 */
export function supportsDirectoryExport(): boolean {
  return Boolean(window.__picturepickTestExportDirHandle) || typeof window.showDirectoryPicker === 'function';
}

/** 选择导出目标目录；取消/不支持/失败分别返回明确状态。 */
export async function requestExportDirectoryHandle(): Promise<DirectoryPickerOutcome> {
  const injected = window.__picturepickTestExportDirHandle;
  if (injected) return { status: 'picked', handle: injected };
  if (typeof window.showDirectoryPicker !== 'function') return { status: 'unsupported' };
  try {
    const handle = await window.showDirectoryPicker({ mode: 'readwrite', id: 'picturepick-export' });
    return { status: 'picked', handle };
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') return { status: 'cancelled' };
    return { status: 'failed', error };
  }
}

/** 去掉 Windows/通用文件系统非法字符。 */
export function sanitizeFileName(name: string): string {
  const trimmed = name.trim() || 'photo.jpg';
  return trimmed.replace(/[\\/:*?"<>|\u0000-\u001f]/g, '_');
}

function exportFileName(photo: ExportPhotoItem): string {
  return sanitizeFileName(photo.name || fileBaseName(photo.relativePath));
}

function splitExtension(fileName: string): { base: string; extension: string } {
  const dot = fileName.lastIndexOf('.');
  if (dot <= 0) return { base: fileName, extension: '' };
  return { base: fileName.slice(0, dot), extension: fileName.slice(dot) };
}

/** 同名时依次尝试 name、name-1、name-2 …；真实目录会先检查文件是否已存在。 */
async function resolveAvailableName(
  handle: DirectoryHandleLike,
  fileName: string,
  used: Set<string>,
  isMock: boolean,
): Promise<string> {
  const { base, extension } = splitExtension(fileName);
  for (let index = 0; index < 1000; index += 1) {
    const candidate = index === 0 ? fileName : `${base}-${index}${extension}`;
    if (used.has(candidate)) continue;
    if (isMock) return candidate;
    try {
      // 不存在会抛 NotFoundError → 该名字可用。
      await handle.getFileHandle(candidate);
    } catch {
      return candidate;
    }
  }
  throw new Error('同名文件过多，无法生成唯一文件名');
}

function isMockHandle(handle: DirectoryHandleLike): boolean {
  return (
    typeof handle.isSameEntry !== 'function' || window.__picturepickTestExportDirHandle === handle
  );
}

/** 写入目标文件夹；返回文件夹名与成功写入数量。 */
export async function exportPhotosToDirectory(
  handle: DirectoryHandleLike,
  photos: ExportPhotoItem[],
  getBlob: GetExportBlob,
  onProgress?: (info: ExportProgressInfo) => void,
): Promise<{ folderName: string; count: number }> {
  const isMock = isMockHandle(handle);
  const used = new Set<string>();
  for (let index = 0; index < photos.length; index += 1) {
    const photo = photos[index];
    onProgress?.({ done: index, total: photos.length, currentName: photo.name });
    const blob = await getBlob(photo);
    const fileName = await resolveAvailableName(handle, exportFileName(photo), used, isMock);
    used.add(fileName);
    const fileHandle = await handle.getFileHandle(fileName, { create: true });
    const writable = await fileHandle.createWritable();
    await writable.write(blob);
    await writable.close();
    onProgress?.({ done: index + 1, total: photos.length, currentName: photo.name });
    // 让出主线程，保证进度 UI 能刷新。
    await new Promise((resolve) => setTimeout(resolve, 0));
  }
  return { folderName: handle.name ?? '所选文件夹', count: photos.length };
}

/** 触发一次浏览器下载。 */
export function triggerBlobDownload(blob: Blob, fileName: string): void {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = fileName;
  anchor.rel = 'noopener';
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  // 延迟释放，避免下载尚未开始就失效。
  window.setTimeout(() => URL.revokeObjectURL(url), 60_000);
}

/** 降级方案：逐张下载。 */
export async function exportPhotosAsDownloads(
  photos: ExportPhotoItem[],
  getBlob: GetExportBlob,
  onProgress?: (info: ExportProgressInfo) => void,
): Promise<{ count: number }> {
  for (let index = 0; index < photos.length; index += 1) {
    const photo = photos[index];
    onProgress?.({ done: index, total: photos.length, currentName: photo.name });
    const blob = await getBlob(photo);
    triggerBlobDownload(blob, exportFileName(photo));
    onProgress?.({ done: index + 1, total: photos.length, currentName: photo.name });
    // 连续触发下载需要间隔，否则可能被浏览器拦截。
    await new Promise((resolve) => setTimeout(resolve, 350));
  }
  return { count: photos.length };
}
