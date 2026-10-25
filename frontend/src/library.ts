/**
 * 浏览器端照片图库：保存用户选择目录得到的 File（照片只在本地内存），
 * 负责 object URL 的创建/复用/释放，以及 photoId ↔ File 映射。
 *
 * - 原图 URL：URL.createObjectURL(File)
 * - 缩略图 URL：createImageBitmap(resize) + canvas.toBlob 生成小尺寸 JPEG object URL，
 *   避免把全尺寸原图塞进胶片条 / 弹窗 / 结果列表（内存与渲染压力大）。
 *
 * Photo ID 契约（docs/API.md §0/§4）：sha1(relativePath)[:16]（UTF-8，小写 hex）。
 * 为防后端对路径做归一化导致 ID 不一致，进入选片页后可用 reconcile()
 * 按 relativePath 把图库条目重新绑定到后端返回的 photoId 上。
 */
import type { BrowserPhotoInput } from './types';

export interface LibraryEntry {
  id: string;
  file: File;
  relativePath: string;
  name: string;
}

const PHOTO_EXTENSIONS = ['.jpg', '.jpeg'];
const THUMB_MAX_EDGE = 360;
const THUMB_QUALITY = 0.82;

/** 扩展名是否为 .jpg / .jpeg（大小写不敏感）。 */
export function isPhotoFile(file: File): boolean {
  const lower = file.name.toLowerCase();
  return PHOTO_EXTENSIONS.some((extension) => lower.endsWith(extension));
}

/** 取 webkitRelativePath（统一为正斜杠）；退化时用文件名。 */
export function relativePathOf(file: File): string {
  const withRelative = file as File & { webkitRelativePath?: string };
  const relative = withRelative.webkitRelativePath;
  if (typeof relative === 'string' && relative.trim()) return relative.replace(/\\/g, '/');
  return file.name;
}

/** 相对路径的根文件夹名（第一段）。 */
export function rootFolderName(relativePath: string): string {
  const parts = relativePath.split('/').filter(Boolean);
  return parts.length > 1 ? parts[0] : '照片文件夹';
}

export function fileBaseName(relativePath: string): string {
  const parts = relativePath.split('/').filter(Boolean);
  return parts.length ? parts[parts.length - 1] : relativePath;
}

/** sha1(relativePath)[:16]，与后端 browser 会话的照片 ID 规则一致。 */
export async function computePhotoId(relativePath: string): Promise<string> {
  const data = new TextEncoder().encode(relativePath);
  if (globalThis.crypto?.subtle) {
    const digest = await crypto.subtle.digest('SHA-1', data);
    return Array.from(new Uint8Array(digest))
      .map((byte) => byte.toString(16).padStart(2, '0'))
      .join('')
      .slice(0, 16);
  }
  // 极少数非安全上下文没有 crypto.subtle：用相对路径做一个稳定但不同的键，
  // 进入选片页后 reconcile() 会按 relativePath 修正为后端 ID。
  let hash = 0;
  for (let index = 0; index < relativePath.length; index += 1) {
    hash = (hash * 31 + relativePath.charCodeAt(index)) >>> 0;
  }
  return `local-${hash.toString(16).padStart(8, '0')}`.slice(0, 16);
}

export interface PreparedLibrary {
  entries: LibraryEntry[];
  photos: BrowserPhotoInput[];
}

type ThumbListener = (photoId: string, url: string) => void;

export class PhotoLibrary {
  private readonly entriesById = new Map<string, LibraryEntry>();
  private readonly entriesByPath = new Map<string, LibraryEntry>();
  private readonly originalUrls = new Map<LibraryEntry, string>();
  private readonly thumbUrls = new Map<LibraryEntry, string>();
  private readonly thumbTasks = new Map<LibraryEntry, Promise<string>>();
  private readonly thumbListeners = new Set<ThumbListener>();
  private sessionId: string | null = null;

  get size(): number {
    return this.entriesById.size;
  }

  hasFiles(): boolean {
    return this.entriesById.size > 0;
  }

  get currentSessionId(): string | null {
    return this.sessionId;
  }

  setSession(sessionId: string): void {
    this.sessionId = sessionId;
  }

  /** 载入一组 File：过滤 jpg/jpeg、按相对路径排序、计算照片 ID。会清空旧图库。 */
  async loadFiles(files: File[]): Promise<PreparedLibrary> {
    this.clear();
    const selected = files
      .filter(isPhotoFile)
      .map((file) => ({ file, relativePath: relativePathOf(file) }))
      .sort((a, b) => a.relativePath.toLowerCase().localeCompare(b.relativePath.toLowerCase()));

    const withIds = await Promise.all(
      selected.map(async (item) => ({ ...item, id: await computePhotoId(item.relativePath) })),
    );

    const entries: LibraryEntry[] = [];
    const photos: BrowserPhotoInput[] = [];
    for (const item of withIds) {
      if (this.entriesById.has(item.id)) continue; // 同一相对路径只保留一条
      const entry: LibraryEntry = {
        id: item.id,
        file: item.file,
        relativePath: item.relativePath,
        name: item.file.name,
      };
      this.entriesById.set(entry.id, entry);
      this.entriesByPath.set(entry.relativePath, entry);
      entries.push(entry);
      photos.push({
        relativePath: entry.relativePath,
        sizeBytes: item.file.size,
        lastModified: item.file.lastModified,
      });
    }
    return { entries, photos };
  }

  getEntry(photoId: string): LibraryEntry | undefined {
    return this.entriesById.get(photoId);
  }

  getFile(photoId: string): File | undefined {
    return this.entriesById.get(photoId)?.file;
  }

  has(photoId: string): boolean {
    return this.entriesById.has(photoId);
  }

  /** 原图 object URL（同一 File 只创建一次）。 */
  getUrl(photoId: string): string {
    const entry = this.entriesById.get(photoId);
    if (!entry) return '';
    const existing = this.originalUrls.get(entry);
    if (existing) return existing;
    const url = URL.createObjectURL(entry.file);
    this.originalUrls.set(entry, url);
    return url;
  }

  /** 缩略图 object URL；未生成时返回原图 URL（并触发后台生成）。 */
  getThumbUrl(photoId: string): string {
    const entry = this.entriesById.get(photoId);
    if (!entry) return '';
    const thumb = this.thumbUrls.get(entry);
    if (thumb) return thumb;
    void this.ensureThumb(photoId).catch(() => undefined);
    const original = this.originalUrls.get(entry);
    if (original) return original;
    const url = URL.createObjectURL(entry.file);
    this.originalUrls.set(entry, url);
    return url;
  }

  /** 确保缩略图已生成；返回缩略图 URL。 */
  async ensureThumb(photoId: string): Promise<string> {
    const entry = this.entriesById.get(photoId);
    if (!entry) throw new Error('照片不存在');
    const ready = this.thumbUrls.get(entry);
    if (ready) return ready;
    const pending = this.thumbTasks.get(entry);
    if (pending) return pending;
    const task = this.createThumb(entry);
    this.thumbTasks.set(entry, task);
    try {
      return await task;
    } finally {
      this.thumbTasks.delete(entry);
    }
  }

  /** 批量预生成缩略图（忽略单张失败）。 */
  async ensureThumbs(photoIds: string[]): Promise<void> {
    await Promise.all(
      photoIds.map((photoId) =>
        this.ensureThumb(photoId).catch(() => undefined),
      ),
    );
  }

  /** 订阅缩略图就绪事件；返回取消订阅函数。 */
  onThumbReady(listener: ThumbListener): () => void {
    this.thumbListeners.add(listener);
    return () => this.thumbListeners.delete(listener);
  }

  findByRelativePath(relativePath: string): LibraryEntry | undefined {
    const normalized = relativePath.replace(/\\/g, '/');
    return (
      this.entriesByPath.get(normalized) ??
      [...this.entriesByPath.values()].find(
        (entry) => entry.relativePath.toLowerCase() === normalized.toLowerCase(),
      )
    );
  }

  /**
   * 按后端返回的 (id, relativePath) 校正映射，兼容后端对路径归一化导致
   * 前端预计算 ID 与后端 ID 不一致的情况。
   */
  reconcile(items: Array<{ id: string; relativePath: string }>): void {
    for (const item of items) {
      if (this.entriesById.has(item.id)) continue;
      const entry = this.findByRelativePath(item.relativePath);
      if (!entry) continue;
      if (entry.id !== item.id) this.entriesById.delete(entry.id);
      entry.id = item.id;
      this.entriesById.set(item.id, entry);
      this.entriesByPath.set(entry.relativePath, entry);
    }
  }

  clear(): void {
    for (const url of this.originalUrls.values()) URL.revokeObjectURL(url);
    for (const url of this.thumbUrls.values()) URL.revokeObjectURL(url);
    this.originalUrls.clear();
    this.thumbUrls.clear();
    this.thumbTasks.clear();
    this.entriesById.clear();
    this.entriesByPath.clear();
    this.sessionId = null;
  }

  private async createThumb(entry: LibraryEntry): Promise<string> {
    try {
      const bitmap = await createImageBitmap(entry.file, {
        resizeWidth: THUMB_MAX_EDGE,
        resizeQuality: 'medium',
      });
      const canvas = document.createElement('canvas');
      canvas.width = bitmap.width;
      canvas.height = bitmap.height;
      const context = canvas.getContext('2d');
      if (!context) throw new Error('无法创建缩略图画布');
      context.drawImage(bitmap, 0, 0);
      bitmap.close?.();
      const blob = await new Promise<Blob | null>((resolve) => {
        canvas.toBlob(resolve, 'image/jpeg', THUMB_QUALITY);
      });
      if (!blob) throw new Error('缩略图编码失败');
      const url = URL.createObjectURL(blob);
      this.thumbUrls.set(entry, url);
      for (const listener of this.thumbListeners) listener(entry.id, url);
      return url;
    } catch {
      // 缩略图失败时退化为原图 URL（仍可用，只是体积大）。
      const original = this.originalUrls.get(entry);
      if (original) return original;
      const url = URL.createObjectURL(entry.file);
      this.originalUrls.set(entry, url);
      return url;
    }
  }
}
