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
