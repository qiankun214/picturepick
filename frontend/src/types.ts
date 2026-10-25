/**
 * 与 docs/API.md（第 2 版）严格对应的数据结构。
 * 任何接口字段变化都必须先改契约，再同步本文件。
 */

export type SessionPhase = 'picking' | 'finished';

/** 会话来源：后端扫描本机目录 / 浏览器原生目录选择。 */
export type SessionOrigin = 'server' | 'browser';

export interface RoundSummary {
  index: number;
  photoCount: number;
  viewedCount: number;
  pickedCount: number;
}

export interface SessionSummary {
  id: string;
  /** 第 2 版字段；旧后端可能缺失，前端按 server 兜底。 */
  origin: SessionOrigin;
  /** 展示用来源标识：server 为绝对路径，browser 为所选文件夹名。 */
  sourceLabel: string;
  root: string;
  recursive: boolean;
  createdAt: string;
  phase: SessionPhase;
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
  /** server 会话为绝对路径；browser 会话为空字符串。 */
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

export interface HealthResponse {
  status: string;
  name: string;
  version: string;
}

export interface CurrentSessionResponse {
  session: SessionSummary | null;
}

/** 浏览器选择目录后提交给后端的单张照片元数据（不上传文件内容）。 */
export interface BrowserPhotoInput {
  relativePath: string;
  sizeBytes: number;
  lastModified: number;
}

export interface CreateBrowserSessionRequest {
  source: 'browser';
  label: string;
  recursive: boolean;
  photos: BrowserPhotoInput[];
}

export interface CreateServerSessionRequest {
  source?: 'server';
  directory: string;
  recursive: boolean;
}

export type CreateSessionRequest = CreateBrowserSessionRequest | CreateServerSessionRequest;

/** 图片变体：原图 / 缩略图。 */
export type ImageVariant = 'original' | 'thumb';

/** 统一的图片地址解析器：browser 用 object URL，server 用 /image 接口。 */
export type ResolveImageUrl = (photoId: string, variant: ImageVariant) => string;

/** 后端统一错误体（docs/API.md §1）。 */
export interface ApiErrorDetail {
  code: string;
  message: string;
}

export interface ApiErrorBody {
  detail?: ApiErrorDetail | string;
}
