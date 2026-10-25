/**
 * 与 docs/API.md（第 3 版）严格对应的数据结构。
 *
 * 第 3 版修正：浏览器只负责选择「绝对目录路径」，照片由本机后端扫描并通过
 * `/api/sessions/{sid}/photos/{pid}/image` 按需提供；浏览器不读取、不上传照片。
 * 前端不再创建 browser 来源会话（后端保留兼容），导出改为后端复制文件。
 */

export type SessionPhase = 'picking' | 'finished';

/** 会话来源：server（后端扫描本机目录）/ browser（v2 旧会话，前端不再创建）。 */
export type SessionOrigin = 'server' | 'browser';

export interface RoundSummary {
  index: number;
  photoCount: number;
  viewedCount: number;
  pickedCount: number;
}

export interface SessionSummary {
  id: string;
  origin: SessionOrigin;
  sourceLabel: string;
  root: string;
  recursive: boolean;
  createdAt: string;
  phase: SessionPhase;
  /** 是否通过 POST /api/sessions/restore 从结果 JSON 恢复（默认 false）。 */
  restored?: boolean;
  roundIndex: number;
  roundCount: number;
  libraryTotal: number;
  roundPhotoCount: number;
  viewedCount: number;
  pickCount: number;
  /** 本会话已采纳识别的照片数（AI 识鸟；旧会话可能缺省）。 */
  birdIdentifiedCount?: number;
  rounds: RoundSummary[];
}

export interface PhotoItem {
  id: string;
  name: string;
  relativePath: string;
  sizeBytes: number;
  pickCount: number;
  picked: boolean;
  viewed: boolean;
  /** AI 识鸟结果（已采纳时存在；未识别为 null/缺省）。 */
  bird?: BirdObservation | null;
}

export interface RoundPhotosResponse {
  roundIndex: number;
  total: number;
  offset: number;
  limit: number;
  photos: PhotoItem[];
}

export interface PickedPhoto {
  id: string;
  name: string;
  relativePath: string;
  absolutePath: string;
  pickCount: number;
  pickedInRounds: number[];
  /** AI 识鸟结果（结果 JSON 新增可选字段；老 JSON 无此字段）。 */
  bird?: BirdObservation | null;
}

/** 最后一轮仍然保留的照片，供「导出保留照片」使用。 */
export interface LastRoundSummary {
  index: number;
  pickedCount: number;
  pickedPhotoIds: string[];
}

export interface PickResult {
  schemaVersion: number;
  generatedAt: string;
  origin: SessionOrigin;
  sourceDirectory: string;
  recursive: boolean;
  totalPhotos: number;
  roundsPlayed: number;
  pickedPhotoCount: number;
  /** 本会话已采纳识别的照片数（结果 JSON 新增可选字段）。 */
  birdIdentifiedCount?: number;
  lastRound: LastRoundSummary;
  photos: PickedPhoto[];
}

export interface FinishResponse {
  result: PickResult;
  savedPath: string | null;
}

export interface ExportResponse {
  path: string;
}

export interface HealthResponse {
  status: string;
  name: string;
  version: string;
}

export interface CurrentSessionResponse {
  session: SessionSummary | null;
}

/** 创建 server 来源会话（后端扫描目录并提供图片）。 */
export interface CreateServerSessionRequest {
  source?: 'server';
  directory: string;
  recursive: boolean;
}

/** 原生目录对话框返回值（docs/API.md §3）。 */
export interface DialogPickResponse {
  path: string | null;
  cancelled: boolean;
}

/** POST /api/sessions/{sid}/export-photos 请求。 */
export interface ExportPhotosRequest {
  destination: string;
}

export interface ExportedPhotoFile {
  name: string;
  relativePath: string;
  sizeBytes: number;
}

/** POST /api/sessions/{sid}/export-photos 成功响应。 */
export interface ExportPhotosResponse {
  destination: string;
  copied: number;
  files: ExportedPhotoFile[];
}

/** 图片变体：原图 / 缩略图。 */
export type ImageVariant = 'original' | 'thumb';

/** 后端统一错误体（docs/API.md §1）。 */
export interface ApiErrorDetail {
  code: string;
  message: string;
}

export interface ApiErrorBody {
  detail?: ApiErrorDetail | string;
}

/* ------------------------------------------------------------------ */
/* AI 识鸟（Bird ID）—— docs/BIRDID-CONTRACT.md §6.3                   */
/* ------------------------------------------------------------------ */

export type BirdTaskStatus =
  | 'queued'
  | 'running'
  | 'completed'
  | 'failed'
  | 'adopted'
  | 'dismissed';

export type BirdTaskStage = 'queued' | 'preparing' | 'identifying' | 'reference' | 'done';

export interface BirdAlternative {
  species: string;
  speciesLatin: string;
  confidence: number | null;
}

export interface BirdResult {
  identified: boolean;
  /** 科（中文）。 */
  family: string;
  familyLatin: string;
  /** 属（中文）。 */
  genus: string;
  genusLatin: string;
  /** 种（中文）。 */
  species: string;
  speciesLatin: string;
  commonNameEn: string;
  confidence: number | null;
  note: string;
  /** identified=false 时的原因。 */
  reason: string;
  alternatives: BirdAlternative[];
  referenceSource: string;
  referenceNote: string;
}

export interface BirdTask {
  id: string;
  sessionId: string;
  photoId: string;
  photoName: string;
  photoRelativePath: string;
  /** /api/sessions/{sid}/photos/{pid}/image?variant=thumb */
  thumbnailUrl: string;
  status: BirdTaskStatus;
  stage: BirdTaskStage;
  /** 0–100 */
  progress: number;
  /** 1-based，仅 queued 有；其它为 null。 */
  queuePosition: number | null;
  createdAt: string;
  startedAt: string | null;
  finishedAt: string | null;
  elapsedMs: number;
  model: string;
  result: BirdResult | null;
  error: { code: string; message: string } | null;
  adopted: boolean;
  referenceAvailable: boolean;
  /** /api/sessions/{sid}/bird-tasks/{tid}/reference */
  referenceUrl: string | null;
}

export interface BirdObservation {
  family: string;
  familyLatin: string;
  genus: string;
  genusLatin: string;
  species: string;
  speciesLatin: string;
  commonNameEn: string;
  confidence: number | null;
  note: string;
  referenceSource: string;
  model: string;
  /** ISO-8601 */
  adoptedAt: string;
  exifWritten: boolean;
  exifError: string | null;
}

/** 队列计数（GET /api/bird/status 与 GET .../bird-tasks 都返回）。 */
export interface BirdQueueCount {
  queued: number;
  running: number;
}

export type BirdKeySource = 'env' | 'config' | 'mock' | null;

export type BirdEndpointProtocol = 'chat' | 'responses';

/** GET /api/bird/status / PUT /api/bird/config 响应。 */
export interface BirdStatus {
  configured: boolean;
  model: string;
  /** 完整 API 端点 URL（后端原样 POST，不做路径拼接）。 */
  endpoint: string;
  /** 端点协议：Chat Completions / Responses API；不支持时为 null。 */
  endpointProtocol: BirdEndpointProtocol | null;
  /** 端点不是受支持的完整接口 URL 时的提示。 */
  endpointWarning: string | null;
  keyMasked: string | null;
  keySource: BirdKeySource;
  mock: boolean;
  queue: BirdQueueCount;
}

/** PUT /api/bird/config 请求（字段均可缺省；null / 空字符串 = 清除该字段）。 */
export interface BirdConfigRequest {
  apiKey?: string | null;
  /** 完整 API 端点（/chat/completions 或 /responses 结尾）。 */
  endpoint?: string | null;
  model?: string | null;
  mock?: boolean;
}

/** POST /api/bird/verify 响应（失败也 200）。 */
export interface BirdVerifyResponse {
  ok: boolean;
  message: string;
  latencyMs: number | null;
  model: string;
}

/** GET /api/sessions/{sid}/bird-tasks 响应。 */
export interface BirdTasksResponse {
  tasks: BirdTask[];
  queue: BirdQueueCount;
}

/** EXIF 写入结果（采纳响应内嵌）。 */
export interface BirdExifOutcome {
  written: boolean;
  path: string;
  error: string | null;
}

/** POST /api/sessions/{sid}/bird-tasks/{tid}/adopt 响应。 */
export interface BirdAdoptResponse {
  task: BirdTask;
  observation: BirdObservation;
  exif: BirdExifOutcome;
}
