/**
 * docs/API.md（第 2 版）的唯一前端调用封装。
 *
 * - 所有请求都走同源 `/api`（开发期由 Vite 代理到 127.0.0.1:8000）。
 * - 非 2xx 统一抛 ApiError（含 code / status / 面向用户的中文 message）。
 * - 图片、下载等「让浏览器直接发起」的地址通过 URL 构造函数暴露；
 *   browser 来源会话的图片由 PhotoLibrary 的 object URL 提供，不走本模块。
 */
import type {
  ApiErrorBody,
  CreateSessionRequest,
  CurrentSessionResponse,
  FinishResponse,
  HealthResponse,
  ImageVariant,
  PhotoItem,
  PickResult,
  RoundPhotosResponse,
  SessionSummary,
} from './types';

export const API_BASE = '/api';

/** 错误码 → 面向用户的中文文案（docs/API.md §1）。 */
const ERROR_MESSAGES: Record<string, string> = {
  INVALID_PATH: '目录不存在、不是目录或没有读取权限',
  NO_PHOTOS: '没有找到 .jpg / .jpeg 照片',
  INVALID_PHOTOS: '照片列表格式非法，请重新选择文件夹',
  TOO_MANY_PHOTOS: '照片数量超过上限（50000 张），请选择更小的文件夹',
  SESSION_NOT_FOUND: '选片会话不存在或已过期',
  PHOTO_NOT_FOUND: '照片不存在，或文件已被移动/删除',
  ROUND_NOT_FOUND: '轮次不存在',
  NOT_ENOUGH_PICKS: '进入下一轮至少需要保留 2 张照片',
  PHASE_FINISHED: '本轮选片已结束',
  DIALOG_UNAVAILABLE: '系统目录选择框不可用',
  WRITE_FAILED: '写入结果文件失败',
};

export function friendlyMessage(code: string, fallback: string): string {
  return ERROR_MESSAGES[code] ?? fallback;
}

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
  }

  /** 网络层失败（后端未启动 / 连接被拒绝 / 请求被中断）。 */
  get isNetworkError(): boolean {
    return this.status === 0;
  }
}

export function errorText(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error) return error.message;
  return String(error);
}

interface RequestOptions {
  method?: 'GET' | 'POST' | 'PUT' | 'DELETE';
  body?: unknown;
  signal?: AbortSignal;
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = 'GET', body, signal } = options;
  const init: RequestInit = {
    method,
    signal,
    headers: { Accept: 'application/json' },
  };
  if (body !== undefined) {
    init.headers = { ...init.headers, 'Content-Type': 'application/json' };
    init.body = JSON.stringify(body);
  }

  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, init);
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error;
    throw new ApiError(0, 'NETWORK', '无法连接 PicturePick 本地服务，请确认后端已启动');
  }

  if (!response.ok) {
    let code = 'HTTP_ERROR';
    let message = `请求失败（HTTP ${response.status}）`;
    try {
      const payload = (await response.json()) as ApiErrorBody;
      const detail = payload?.detail;
      if (typeof detail === 'string' && detail.trim()) {
        message = detail;
      } else if (detail && typeof detail === 'object') {
        if (typeof detail.code === 'string' && detail.code) code = detail.code;
        if (typeof detail.message === 'string' && detail.message.trim()) message = detail.message;
      }
    } catch {
      // 非 JSON 错误体（例如代理返回的纯文本），保留默认文案。
    }
    throw new ApiError(response.status, code, friendlyMessage(code, message));
  }

  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

/* ------------------------------------------------------------------ */
/* 系统接口                                                            */
/* ------------------------------------------------------------------ */

export function health(signal?: AbortSignal): Promise<HealthResponse> {
  return request<HealthResponse>('/health', { signal });
}

/* ------------------------------------------------------------------ */
/* 会话接口                                                            */
/* ------------------------------------------------------------------ */

/**
 * 创建会话：
 * - browser：只提交照片元数据（文件留在浏览器内存）→ origin=browser
 * - server ：后端扫描本机目录（CLI / 桌面端 --dir 场景）
 */
export function createSession(payload: CreateSessionRequest): Promise<SessionSummary> {
  return request<SessionSummary>('/sessions', { method: 'POST', body: payload });
}

export function getCurrentSession(signal?: AbortSignal): Promise<CurrentSessionResponse> {
  return request<CurrentSessionResponse>('/sessions/current', { signal });
}

export function getSession(sessionId: string, signal?: AbortSignal): Promise<SessionSummary> {
  return request<SessionSummary>(`/sessions/${encodeURIComponent(sessionId)}`, { signal });
}

export function getRoundPhotos(
  sessionId: string,
  roundIndex: number,
  offset = 0,
  limit = 500,
  signal?: AbortSignal,
): Promise<RoundPhotosResponse> {
  const query = new URLSearchParams({ offset: String(offset), limit: String(limit) });
  return request<RoundPhotosResponse>(
    `/sessions/${encodeURIComponent(sessionId)}/rounds/${roundIndex}/photos?${query.toString()}`,
    { signal },
  );
}

/** 拉取一整轮的照片（自动翻页，顺序即放映顺序）。 */
export async function getAllRoundPhotos(
  sessionId: string,
  roundIndex: number,
  pageSize = 500,
  signal?: AbortSignal,
): Promise<PhotoItem[]> {
  const collected: PhotoItem[] = [];
  let offset = 0;
  for (;;) {
    const page = await getRoundPhotos(sessionId, roundIndex, offset, pageSize, signal);
    collected.push(...page.photos);
    const total = Number.isFinite(page.total) ? page.total : collected.length;
    if (collected.length >= total || page.photos.length === 0) break;
    offset += page.photos.length;
  }
  return collected;
}

export function markViewed(sessionId: string, photoId: string): Promise<SessionSummary> {
  return request<SessionSummary>(
    `/sessions/${encodeURIComponent(sessionId)}/photos/${encodeURIComponent(photoId)}/viewed`,
    { method: 'POST' },
  );
}

export function pickPhoto(sessionId: string, photoId: string): Promise<SessionSummary> {
  return request<SessionSummary>(
    `/sessions/${encodeURIComponent(sessionId)}/photos/${encodeURIComponent(photoId)}/pick`,
    { method: 'PUT' },
  );
}

export function unpickPhoto(sessionId: string, photoId: string): Promise<SessionSummary> {
  return request<SessionSummary>(
    `/sessions/${encodeURIComponent(sessionId)}/photos/${encodeURIComponent(photoId)}/pick`,
    { method: 'DELETE' },
  );
}

export function nextRound(sessionId: string): Promise<SessionSummary> {
  return request<SessionSummary>(`/sessions/${encodeURIComponent(sessionId)}/rounds/next`, {
    method: 'POST',
  });
}

export function finishSession(sessionId: string): Promise<FinishResponse> {
  return request<FinishResponse>(`/sessions/${encodeURIComponent(sessionId)}/finish`, {
    method: 'POST',
  });
}

export function getResult(sessionId: string, signal?: AbortSignal): Promise<PickResult> {
  return request<PickResult>(`/sessions/${encodeURIComponent(sessionId)}/result`, { signal });
}

/* ------------------------------------------------------------------ */
/* 直连浏览器的 URL（图片 / 下载），不经过 fetch                        */
/* ------------------------------------------------------------------ */

/** server 会话的图片地址。cacheBust 用于「重试加载」时绕过浏览器缓存。 */
export function photoImageUrl(
  sessionId: string,
  photoId: string,
  variant: ImageVariant = 'original',
  cacheBust?: number,
): string {
  const query = new URLSearchParams({ variant });
  if (cacheBust !== undefined) query.set('_v', String(cacheBust));
  return `${API_BASE}/sessions/${encodeURIComponent(sessionId)}/photos/${encodeURIComponent(photoId)}/image?${query.toString()}`;
}

export function resultDownloadUrl(sessionId: string): string {
  return `${API_BASE}/sessions/${encodeURIComponent(sessionId)}/result?download=1`;
}

/** server 会话导出照片时，取回原图 Blob。 */
export async function fetchPhotoBlob(
  sessionId: string,
  photoId: string,
  variant: ImageVariant = 'original',
): Promise<Blob> {
  const response = await fetch(photoImageUrl(sessionId, photoId, variant));
  if (!response.ok) {
    let message = '照片读取失败';
    try {
      const payload = (await response.json()) as ApiErrorBody;
      const detail = payload?.detail;
      if (detail && typeof detail === 'object' && detail.message) message = detail.message;
    } catch {
      // 保留默认文案。
    }
    throw new ApiError(response.status, 'PHOTO_NOT_FOUND', friendlyMessage('PHOTO_NOT_FOUND', message));
  }
  return response.blob();
}
