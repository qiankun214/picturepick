/**
 * 与 docs/API.md（v1）严格对应的数据结构。
 * 任何接口字段变化都必须先改契约，再同步本文件。
 */

export type SessionPhase = 'picking' | 'finished';

export interface RoundSummary {
  index: number;
  photoCount: number;
  viewedCount: number;
  pickedCount: number;
}

export interface SessionSummary {
  id: string;
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
  absolutePath: string;
  pickCount: number;
  pickedInRounds: number[];
}

export interface PickResult {
  schemaVersion: number;
  generatedAt: string;
  sourceDirectory: string;
  recursive: boolean;
  totalPhotos: number;
  roundsPlayed: number;
  pickedPhotoCount: number;
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

export interface FsRoot {
  name: string;
  path: string;
}

export interface FsRootsResponse {
  home: string;
  startPath: string;
  roots: FsRoot[];
}

export interface FsEntry {
  name: string;
  path: string;
  isDir: boolean;
}

export interface FsListResponse {
  path: string;
  parent: string | null;
  entries: FsEntry[];
}

export interface DialogPickResponse {
  path: string | null;
  cancelled: boolean;
}

export interface CurrentSessionResponse {
  session: SessionSummary | null;
}

/** 后端统一错误体（docs/API.md §1）。 */
export interface ApiErrorDetail {
  code: string;
  message: string;
}

export interface ApiErrorBody {
  detail?: ApiErrorDetail | string;
}
