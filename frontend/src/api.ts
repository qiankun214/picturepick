/**
 * docs/API.md（第 3 版）的唯一前端调用封装。
 *
 * - 所有请求都走同源 `/api`（开发期由 Vite 代理到 127.0.0.1:8000）。
 * - 非 2xx 统一抛 ApiError（含 code / status / 面向用户的中文 message）。
 * - 图片、下载等「让浏览器直接发起」的地址通过 URL 构造函数暴露；
 *   照片本体始终由本机后端提供/复制，浏览器不读取本地文件。
 */
import type {
  ApiErrorBody,
  BirdAdoptResponse,
  BirdConfigRequest,
  BirdStatus,
  BirdTask,
  BirdTasksResponse,
  BirdVerifyResponse,
  CreateServerSessionRequest,
  CurrentSessionResponse,
  DialogPickResponse,
  ExportPhotosResponse,
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
  INVALID_PATH: '目录不存在、不是目录或没有写入权限',
  NO_PHOTOS: '该目录下没有找到 .jpg / .jpeg 照片',
  INVALID_PHOTOS: '照片列表格式非法',
  TOO_MANY_PHOTOS: '照片数量超过上限（50000 张）',
  SESSION_NOT_FOUND: '选片会话不存在或已过期',
  PHOTO_NOT_FOUND: '照片不存在，或文件已被移动/删除',
  ROUND_NOT_FOUND: '轮次不存在',
  NOT_ENOUGH_PICKS: '进入下一轮至少需要保留 2 张照片',
  PHASE_FINISHED: '本轮选片已结束',
  DIALOG_UNAVAILABLE: '系统文件夹对话框不可用',
  WRITE_FAILED: '写入/复制文件失败',
  NO_PICKED_PHOTOS: '最后一轮没有保留照片，无需导出',
  INVALID_SOURCE: '当前会话来源不支持该操作（仅支持后端扫描的选片会话）',
  INVALID_RESTORE: 'JSON 内容不完整或格式不正确，无法恢复现场',
  UNSUPPORTED_SCHEMA: 'JSON 版本不受支持（需要 schemaVersion=1）',
  PHASE_PICKING: '当前会话已在选片中，无需继续筛选',
  // AI 识鸟（docs/BIRDID-CONTRACT.md §6）
  INVALID_CONFIG: 'AI 识鸟配置不合法',
  BIRD_NOT_CONFIGURED: '尚未配置 AI 识鸟，请先填写火山方舟 API Key',
  BIRD_TASK_NOT_FOUND: 'AI 识鸟任务不存在或已失效',
  BIRD_TASK_NOT_COMPLETED: '该任务尚未完成识别，暂时不能采纳',
  BIRD_TASK_ADOPTED: '该任务已采纳，不能忽略',
  BIRD_RESULT_UNAVAILABLE: '这次识别没有给出鸟种，无法采纳',
  BIRD_REFERENCE_UNAVAILABLE: '没有可用的参考照片',
  BIRD_PARSE_ERROR: 'AI 返回内容无法解析，请重试',
  BIRD_API_ERROR: 'AI 识鸟服务调用失败，请检查 API Key 与模型名',
  BIRD_API_TIMEOUT: 'AI 识鸟请求超时，请稍后重试',
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

/** 让本机后端弹出系统原生文件夹对话框，返回绝对路径；不可用时 501。 */
export function pickDirectoryDialog(signal?: AbortSignal): Promise<DialogPickResponse> {
  return request<DialogPickResponse>('/dialog/pick-directory', { method: 'POST', signal });
}

/* ------------------------------------------------------------------ */
/* 会话接口                                                            */
/* ------------------------------------------------------------------ */

/** 创建 server 来源会话：后端扫描绝对目录并提供图片。 */
export function createSession(payload: CreateServerSessionRequest): Promise<SessionSummary> {
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

/** 从之前保存的结果 JSON（PickResult）恢复现场，返回 restored=true 的 finished 会话。 */
export function restoreSession(result: Record<string, unknown>): Promise<SessionSummary> {
  return request<SessionSummary>('/sessions/restore', { method: 'POST', body: result });
}

/** 在已结束（finished）的 server 会话上继续筛选：用最后一轮保留照片开启新一轮。 */
export function resumeSession(sessionId: string): Promise<SessionSummary> {
  return request<SessionSummary>(`/sessions/${encodeURIComponent(sessionId)}/resume`, {
    method: 'POST',
  });
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

/** 把最后一轮保留的照片由后端复制到指定目录（docs/API.md §4 export-photos）。 */
export function exportPhotos(sessionId: string, destination: string): Promise<ExportPhotosResponse> {
  return request<ExportPhotosResponse>(`/sessions/${encodeURIComponent(sessionId)}/export-photos`, {
    method: 'POST',
    body: { destination },
  });
}

/* ------------------------------------------------------------------ */
/* AI 识鸟（docs/BIRDID-CONTRACT.md §6）                                */
/* ------------------------------------------------------------------ */

/** GET /api/bird/status：配置状态 + 队列计数（永远 200）。 */
export function getBirdStatus(signal?: AbortSignal): Promise<BirdStatus> {
  return request<BirdStatus>('/bird/status', { signal });
}

/** PUT /api/bird/config：保存配置，返回最新状态。 */
export function updateBirdConfig(payload: BirdConfigRequest): Promise<BirdStatus> {
  return request<BirdStatus>('/bird/config', { method: 'PUT', body: payload });
}

/** POST /api/bird/verify：用当前配置发一个极小的真实请求验证连通性。 */
export function verifyBirdConfig(): Promise<BirdVerifyResponse> {
  return request<BirdVerifyResponse>('/bird/verify', { method: 'POST' });
}

/** POST /api/sessions/{sid}/bird-tasks：提交识别（同一照片的 active 任务会被后端复用）。 */
export function createBirdTask(sessionId: string, photoId: string): Promise<BirdTask> {
  return request<BirdTask>(`/sessions/${encodeURIComponent(sessionId)}/bird-tasks`, {
    method: 'POST',
    body: { photoId },
  });
}

/** GET /api/sessions/{sid}/bird-tasks：任务列表（后端已按运行/排队/创建时间排序）。 */
export function getBirdTasks(sessionId: string, signal?: AbortSignal): Promise<BirdTasksResponse> {
  return request<BirdTasksResponse>(
    `/sessions/${encodeURIComponent(sessionId)}/bird-tasks`,
    { signal },
  );
}

/** POST .../bird-tasks/{tid}/adopt：采纳结果并写入照片 EXIF。 */
export function adoptBirdTask(sessionId: string, taskId: string): Promise<BirdAdoptResponse> {
  return request<BirdAdoptResponse>(
    `/sessions/${encodeURIComponent(sessionId)}/bird-tasks/${encodeURIComponent(taskId)}/adopt`,
    { method: 'POST' },
  );
}

/** POST .../bird-tasks/{tid}/dismiss：忽略任务（幂等）。 */
export function dismissBirdTask(sessionId: string, taskId: string): Promise<BirdTask> {
  return request<BirdTask>(
    `/sessions/${encodeURIComponent(sessionId)}/bird-tasks/${encodeURIComponent(taskId)}/dismiss`,
    { method: 'POST' },
  );
}

/** 参考照片地址（后端代理，不落盘；浏览器不直连外网图片）。 */
export function birdTaskReferenceUrl(sessionId: string, taskId: string): string {
  return `${API_BASE}/sessions/${encodeURIComponent(sessionId)}/bird-tasks/${encodeURIComponent(taskId)}/reference`;
}

/* ------------------------------------------------------------------ */
/* 直连浏览器的 URL（图片 / 下载），不经过 fetch                        */
/* ------------------------------------------------------------------ */

/** 照片图片地址（原图 / 缩略图）。 */
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

export function photoThumbUrl(sessionId: string, photoId: string): string {
  return photoImageUrl(sessionId, photoId, 'thumb');
}

export function resultDownloadUrl(sessionId: string): string {
  return `${API_BASE}/sessions/${encodeURIComponent(sessionId)}/result?download=1`;
}
