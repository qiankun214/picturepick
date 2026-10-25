/**
 * 前端本地状态与偏好持久化（localStorage）。
 * 会话数据以服务端为准；这里只保存「设备偏好」和「最近目录」。
 */

const PREFERENCES_KEY = 'picturepick.preferences.v1';

export interface Preferences {
  /** 自动放映间隔（秒），1–120。 */
  intervalSeconds: number;
  /** 是否递归扫描子目录。 */
  recursive: boolean;
  /** 最近使用过的目录（最新的在前，最多 8 条）。 */
  recentDirectories: string[];
}

const DEFAULT_PREFERENCES: Preferences = {
  intervalSeconds: 3,
  recursive: true,
  recentDirectories: [],
};

const MAX_RECENT_DIRECTORIES = 8;
export const MIN_INTERVAL_SECONDS = 1;
export const MAX_INTERVAL_SECONDS = 120;

function getStorage(): Storage | null {
  try {
    // 某些隐私模式/沙箱下访问 localStorage 会抛异常，必须降级为内存态。
    return window.localStorage;
  } catch {
    return null;
  }
}

function clampInterval(value: unknown): number {
  const numeric = typeof value === 'number' ? value : Number(value);
  if (!Number.isFinite(numeric)) return DEFAULT_PREFERENCES.intervalSeconds;
  return Math.min(MAX_INTERVAL_SECONDS, Math.max(MIN_INTERVAL_SECONDS, Math.round(numeric)));
}

function sanitizePreferences(input: unknown): Preferences {
  const source = (input ?? {}) as Partial<Preferences>;
  const recent = Array.isArray(source.recentDirectories)
    ? source.recentDirectories.filter((item): item is string => typeof item === 'string' && item.trim().length > 0)
    : [];
  return {
    intervalSeconds: clampInterval(source.intervalSeconds),
    recursive: typeof source.recursive === 'boolean' ? source.recursive : DEFAULT_PREFERENCES.recursive,
    recentDirectories: recent.slice(0, MAX_RECENT_DIRECTORIES),
  };
}

export function loadPreferences(): Preferences {
  const storage = getStorage();
  if (!storage) return { ...DEFAULT_PREFERENCES, recentDirectories: [] };
  try {
    const raw = storage.getItem(PREFERENCES_KEY);
    if (!raw) return { ...DEFAULT_PREFERENCES, recentDirectories: [] };
    return sanitizePreferences(JSON.parse(raw) as unknown);
  } catch {
    return { ...DEFAULT_PREFERENCES, recentDirectories: [] };
  }
}

export function savePreferences(patch: Partial<Preferences>): Preferences {
  const next = sanitizePreferences({ ...loadPreferences(), ...patch });
  const storage = getStorage();
  if (storage) {
    try {
      storage.setItem(PREFERENCES_KEY, JSON.stringify(next));
    } catch {
      // 配额满/不可写时静默降级。
    }
  }
  return next;
}

export function setIntervalSeconds(seconds: number): number {
  const value = clampInterval(seconds);
  savePreferences({ intervalSeconds: value });
  return value;
}

/** 记住一个目录（去重、最新在前、最多 8 条）。 */
export function rememberDirectory(directory: string): string[] {
  const trimmed = directory.trim();
  if (!trimmed) return loadPreferences().recentDirectories;
  const current = loadPreferences().recentDirectories;
  const deduped = [trimmed, ...current.filter((item) => item.toLowerCase() !== trimmed.toLowerCase())];
  const next = deduped.slice(0, MAX_RECENT_DIRECTORIES);
  savePreferences({ recentDirectories: next });
  return next;
}

export function forgetDirectory(directory: string): string[] {
  const current = loadPreferences().recentDirectories;
  const next = current.filter((item) => item.toLowerCase() !== directory.toLowerCase());
  savePreferences({ recentDirectories: next });
  return next;
}

export function clearRecentDirectories(): void {
  savePreferences({ recentDirectories: [] });
}

/* ------------------------------------------------------------------ */
/* 展示辅助                                                            */
/* ------------------------------------------------------------------ */

/** 取路径最后一段（兼容 Windows 反斜杠与 POSIX 斜杠；盘符根目录返回盘符）。 */
export function baseName(path: string): string {
  const normalized = path.replace(/[\\/]+$/, '');
  if (!normalized) return path;
  const parts = normalized.split(/[\\/]/);
  return parts[parts.length - 1] || normalized;
}

export function dirName(path: string): string {
  const normalized = path.replace(/[\\/]+$/, '');
  const index = Math.max(normalized.lastIndexOf('\\'), normalized.lastIndexOf('/'));
  if (index <= 0) return normalized;
  return normalized.slice(0, index);
}

export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  const digits = value >= 100 || unit === 0 ? 0 : value >= 10 ? 1 : 2;
  return `${value.toFixed(digits)} ${units[unit]}`;
}

/** ISO 时间 → 本地「2026/10/24 15:30」；无法解析时原样返回。 */
export function formatDateTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  const pad = (n: number): string => String(n).padStart(2, '0');
  return `${date.getFullYear()}/${pad(date.getMonth() + 1)}/${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

/** 生成与后端下载文件名一致的 JSON 文件名：picturepick-<目录名>-<yyyyMMdd-HHmmss>.json */
export function buildResultFileName(sourceDirectory: string): string {
  const safeRoot = baseName(sourceDirectory).replace(/[\\/:*?"<>|\s]+/g, '-').replace(/^-+|-+$/g, '') || 'photos';
  const date = new Date();
  const pad = (n: number): string => String(n).padStart(2, '0');
  const stamp = `${date.getFullYear()}${pad(date.getMonth() + 1)}${pad(date.getDate())}-${pad(date.getHours())}${pad(date.getMinutes())}${pad(date.getSeconds())}`;
  return `picturepick-${safeRoot}-${stamp}.json`;
}
