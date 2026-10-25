/**
 * 前端本地偏好与展示辅助。
 * 会话数据以服务端为准；这里只保存「设备偏好」（自动放映间隔）。
 */

const PREFERENCES_KEY = 'picturepick.preferences.v2';

export interface Preferences {
  /** 自动放映间隔（秒），1–120。 */
  intervalSeconds: number;
}

const DEFAULT_PREFERENCES: Preferences = {
  intervalSeconds: 3,
};

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

export function loadPreferences(): Preferences {
  const storage = getStorage();
  if (!storage) return { ...DEFAULT_PREFERENCES };
  try {
    const raw = storage.getItem(PREFERENCES_KEY);
    if (!raw) return { ...DEFAULT_PREFERENCES };
    const parsed = JSON.parse(raw) as Partial<Preferences>;
    return { intervalSeconds: clampInterval(parsed.intervalSeconds) };
  } catch {
    return { ...DEFAULT_PREFERENCES };
  }
}

export function savePreferences(patch: Partial<Preferences>): Preferences {
  const next: Preferences = {
    intervalSeconds: clampInterval(patch.intervalSeconds ?? loadPreferences().intervalSeconds),
  };
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
