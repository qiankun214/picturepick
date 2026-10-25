/**
 * 选片页左侧「图片信息」栏渲染（docs/BIRDID-CONTRACT.md §8.1）。
 *
 * - 基础信息：文件名 / 路径 / 大小 / 尺寸 / pick 状态；
 * - 鸟类识别块：已采纳时显示种（中文 + 拉丁）、科 / 属、置信度、EXIF 写入结果与来源；
 *   未识别时提示「尚未识别 · 按 B 调用 AI 识鸟」。
 */
import { formatBytes } from '../../state';
import type { BirdObservation, PhotoItem } from '../../types';
import { escapeHtml, icon } from '../dom';

export interface PhotoInfoInput {
  photo: PhotoItem | undefined;
  dimensions: { width: number; height: number } | null;
}

function formatConfidence(confidence: number | null): string {
  if (confidence === null || !Number.isFinite(confidence)) return '—';
  const clamped = Math.min(1, Math.max(0, confidence));
  return `${Math.round(clamped * 100)}%`;
}

function formatLatin(value: string | null | undefined): string {
  const latin = String(value ?? '').trim();
  return latin ? `（${latin}）` : '';
}

/** 鸟类识别块（有 photo.bird 显示识别结果，否则显示空状态）。 */
export function renderBirdInfoHtml(bird: BirdObservation | null | undefined): string {
  if (!bird) {
    return `
      <div class="bird-info bird-info--empty" data-testid="bird-info">
        <div class="bird-info__head">${icon('bird')}<span>鸟类识别</span></div>
        <p class="bird-info__empty-text" data-testid="bird-info-empty">尚未识别 · 按 B 调用 AI 识鸟</p>
      </div>
    `;
  }

  const exifBadge = bird.exifWritten
    ? '<span class="bird-info__badge bird-info__badge--ok">已写入 EXIF</span>'
    : `<span class="bird-info__badge bird-info__badge--warn" title="${escapeHtml(bird.exifError ?? '照片 EXIF 写入失败')}">EXIF 写入失败</span>`;
  const species = String(bird.species ?? '').trim() || '未识别';
  const latin = String(bird.speciesLatin ?? '').trim();

  return `
    <div class="bird-info" data-testid="bird-info">
      <div class="bird-info__head">
        ${icon('bird')}<span>鸟类识别</span>
        <span class="bird-info__badge bird-info__badge--ai">AI</span>
      </div>
      <p class="bird-info__species" data-testid="bird-info-species">种：${escapeHtml(species)}${latin ? `（${escapeHtml(latin)}）` : ''}</p>
      <dl class="bird-info__taxa">
        <div class="bird-info__taxon">
          <dt>科</dt>
          <dd>${escapeHtml(String(bird.family ?? '').trim() || '—')}${escapeHtml(formatLatin(bird.familyLatin))}</dd>
        </div>
        <div class="bird-info__taxon">
          <dt>属</dt>
          <dd>${escapeHtml(String(bird.genus ?? '').trim() || '—')}${escapeHtml(formatLatin(bird.genusLatin))}</dd>
        </div>
      </dl>
      <p class="bird-info__meta">
        <span>AI · 置信度 ${formatConfidence(bird.confidence)} · </span>${exifBadge}
      </p>
      ${
        bird.referenceSource
          ? `<p class="bird-info__source">参考照片来源：${escapeHtml(bird.referenceSource)}</p>`
          : ''
      }
      ${bird.model ? `<p class="bird-info__source">模型：${escapeHtml(bird.model)}</p>` : ''}
    </div>
  `;
}

/** 重新渲染整块左栏（照片切换 / pick 状态变化 / 采纳识别后调用）。 */
export function renderPhotoInfo(root: HTMLElement, input: PhotoInfoInput): void {
  const { photo, dimensions } = input;
  const rows: string[] = [];

  if (!photo) {
    rows.push('<p class="photo-info__empty">正在读取图片信息…</p>');
  } else {
    const pickText = photo.picked
      ? `已保留${photo.pickCount > 0 ? ` · 累计 ×${photo.pickCount}` : ''}`
      : '未保留';
    rows.push(`
      <div class="photo-info__section">
        <p class="photo-info__filename" title="${escapeHtml(photo.relativePath)}">${escapeHtml(photo.name)}</p>
        <div class="photo-info__row">
          <span class="photo-info__label">路径</span>
          <span class="photo-info__value photo-info__value--mono" title="${escapeHtml(photo.relativePath)}">${escapeHtml(photo.relativePath)}</span>
        </div>
        <div class="photo-info__row">
          <span class="photo-info__label">大小</span>
          <span class="photo-info__value">${formatBytes(photo.sizeBytes)}</span>
        </div>
        <div class="photo-info__row">
          <span class="photo-info__label">尺寸</span>
          <span class="photo-info__value">${dimensions ? `${dimensions.width} × ${dimensions.height}` : '—'}</span>
        </div>
        <div class="photo-info__row">
          <span class="photo-info__label">状态</span>
          <span class="photo-info__value">${escapeHtml(pickText)}${photo.viewed ? ' · 已浏览' : ''}</span>
        </div>
      </div>
    `);
  }

  rows.push(renderBirdInfoHtml(photo?.bird ?? null));
  root.innerHTML = rows.join('');
}
