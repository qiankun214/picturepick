/**
 * AI 识鸟结果 / 失败 / 进度模态框（docs/BIRDID-CONTRACT.md §8.3）。
 *
 * - completed 且 identified=true：参考照片 + 种 / 科 / 属 / 拉丁名 / 置信度 + 采纳 / 忽略；
 * - completed 且 identified=false：展示模型给出的原因 + 忽略；
 * - failed：错误码 / 信息 + 重新识别 + 忽略；
 * - adopted / dismissed：只读回看，按钮仅「关闭」；
 * - queued / running：展示进度，按钮仅「关闭」。
 */
import * as api from '../../api';
import { birdTaskReferenceUrl, errorText, photoImageUrl } from '../../api';
import type { BirdAdoptResponse, BirdAlternative, BirdTask } from '../../types';
import { escapeHtml, icon } from '../dom';
import { openModal, type ModalAction, type ModalHandle } from '../modal';
import { showToast } from '../toast';

export interface BirdResultModalOptions {
  sessionId: string;
  task: BirdTask;
  /** 采纳成功（含 EXIF 写入失败）后回调，供选片页更新左栏/当前照片/队列。 */
  onAdopted?: (response: BirdAdoptResponse) => void;
  /** 忽略成功回传最新任务。 */
  onDismissed?: (task: BirdTask) => void;
  /** 「重新识别」= 对同一 photoId 新建任务（失败旧任务保留）。 */
  onRetry?: () => void;
}

function clamp01(value: number | null | undefined): number | null {
  if (value === null || value === undefined || !Number.isFinite(value)) return null;
  return Math.min(1, Math.max(0, value));
}

function percentText(value: number | null | undefined): string {
  const clamped = clamp01(value);
  return clamped === null ? '—' : `${Math.round(clamped * 100)}%`;
}

function alternativesHtml(alternatives: BirdAlternative[] | null | undefined): string {
  const items = (alternatives ?? []).filter((item) => item && String(item.species ?? '').trim()).slice(0, 3);
  if (!items.length) return '';
  return `
    <div class="bird-result__alts">
      <p class="bird-result__alts-title">备选可能</p>
      ${items
        .map(
          (item) => `
        <p class="bird-result__alt-item">
          <span>${escapeHtml(item.species)}${item.speciesLatin ? `（${escapeHtml(item.speciesLatin)}）` : ''}</span>
          <span class="bird-result__alt-confidence">${percentText(item.confidence)}</span>
        </p>`,
        )
        .join('')}
    </div>
  `;
}

/** 单个图片框（图片 + 标题）；图片加载失败时显示占位文案。 */
function buildFrame(options: {
  testId: string;
  alt: string;
  caption: string;
  src?: string;
  placeholder?: string;
}): HTMLElement {
  const frame = document.createElement('figure');
  frame.className = 'bird-result__frame';

  const placeholder = document.createElement('div');
  placeholder.className = 'bird-result__placeholder';
  placeholder.innerHTML = `${icon('image')}<span>${escapeHtml(options.placeholder || '图片不可用')}</span>`;
  placeholder.hidden = Boolean(options.src);

  if (options.src) {
    const img = document.createElement('img');
    img.className = 'bird-result__img';
    img.dataset.testid = options.testId;
    img.alt = options.alt;
    img.loading = 'eager';
    img.src = options.src;
    img.addEventListener(
      'error',
      () => {
        img.remove();
        placeholder.hidden = false;
        frame.classList.add('is-missing');
      },
      { once: true },
    );
    frame.appendChild(img);
  }
  frame.appendChild(placeholder);

  const caption = document.createElement('figcaption');
  caption.className = 'bird-result__frame-caption';
  caption.textContent = options.caption;
  frame.appendChild(caption);
  return frame;
}

/** 只允许 http(s) 外链，避免 javascript: 等危险 URL。 */
function safeExternalUrl(raw: string | null | undefined): string | null {
  const text = String(raw ?? '').trim();
  if (!text) return null;
  try {
    const url = new URL(text);
    if (url.protocol !== 'http:' && url.protocol !== 'https:') return null;
    return url.toString();
  } catch {
    return null;
  }
}

/** 同时展示「原照片」与「AI 提供的参考照片」的对照区。 */
function buildCompare(task: BirdTask, sessionId: string): HTMLElement {
  const compare = document.createElement('div');
  compare.className = 'bird-result__compare';

  compare.appendChild(
    buildFrame({
      testId: 'bird-original-image',
      alt: `原照片 ${task.photoName}`,
      caption: `原照片 · ${task.photoName}`,
      src: photoImageUrl(sessionId, task.photoId, 'original'),
      placeholder: '原照片不可用',
    }),
  );

  const hasReference = Boolean(task.referenceAvailable || task.referenceUrl);
  const note =
    String(task.result?.referenceNote ?? '').trim() ||
    'AI 未能提供可访问的参考照片（可在方舟控制台开通「联网搜索」插件后重试）';
  const referenceFrame = buildFrame({
    testId: 'bird-reference-image',
    alt: task.result?.species ? `${task.result.species} 的参考照片` : '参考照片',
    caption: '参考照片（AI 提供）',
    src: hasReference
      ? String(task.referenceUrl || birdTaskReferenceUrl(sessionId, task.id))
      : undefined,
    placeholder: note,
  });

  // AI 只给了网页链接/图片无法下载时，直接展示可点击的参考链接，让用户自己打开确认
  const pageUrl = safeExternalUrl(task.result?.referencePageUrl);
  if (pageUrl) {
    const link = document.createElement('a');
    link.className = 'bird-result__link';
    link.dataset.testid = 'bird-reference-link';
    link.href = pageUrl;
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
    link.textContent = hasReference ? '查看 AI 参考来源页面 ↗' : '打开 AI 提供的参考链接 ↗';
    const placeholder = referenceFrame.querySelector<HTMLElement>('.bird-result__placeholder');
    if (!hasReference && placeholder) placeholder.appendChild(link);
    else referenceFrame.appendChild(link);
  }
  compare.appendChild(referenceFrame);
  return compare;
}

function buildResultInfo(task: BirdTask): HTMLElement {
  const info = document.createElement('div');
  info.className = 'bird-result__info';
  const result = task.result;

  const statusChips: string[] = [];
  if (task.status === 'adopted') statusChips.push('<span class="bird-result__status-pill is-adopted">已采纳</span>');
  if (task.status === 'dismissed') statusChips.push('<span class="bird-result__status-pill is-dismissed">已忽略</span>');

  if (!result || !result.identified) {
    const reason =
      String(result?.reason ?? '').trim() ||
      String(task.error?.message ?? '').trim() ||
      '未能从这张照片中识别出鸟类。';
    info.innerHTML = `
      ${statusChips.length ? `<div class="bird-result__status">${statusChips.join('')}</div>` : ''}
      <h3 class="bird-result__species" data-testid="bird-result-species">未识别到鸟类</h3>
      <p class="bird-result__note">${icon('info')}<span>${escapeHtml(reason)}</span></p>
      <p class="bird-result__model">模型：${escapeHtml(String(task.model ?? '').trim() || '—')}</p>
    `;
    return info;
  }

  const speciesLatin = String(result.speciesLatin ?? '').trim();
  const commonNameEn = String(result.commonNameEn ?? '').trim();
  const confidence = clamp01(result.confidence);
  const familyLatin = String(result.familyLatin ?? '').trim();
  const genusLatin = String(result.genusLatin ?? '').trim();

  info.innerHTML = `
    ${statusChips.length ? `<div class="bird-result__status">${statusChips.join('')}</div>` : ''}
    <h3 class="bird-result__species" data-testid="bird-result-species">${escapeHtml(String(result.species ?? '').trim() || '未命名鸟种')}</h3>
    <p class="bird-result__latin">${escapeHtml(speciesLatin || '—')}${commonNameEn ? ` · ${escapeHtml(commonNameEn)}` : ''}</p>
    <dl class="bird-result__taxa">
      <div class="bird-result__taxon">
        <dt>科</dt>
        <dd data-testid="bird-result-family">${escapeHtml(String(result.family ?? '').trim() || '—')}${familyLatin ? `（${escapeHtml(familyLatin)}）` : ''}</dd>
      </div>
      <div class="bird-result__taxon">
        <dt>属</dt>
        <dd data-testid="bird-result-genus">${escapeHtml(String(result.genus ?? '').trim() || '—')}${genusLatin ? `（${escapeHtml(genusLatin)}）` : ''}</dd>
      </div>
    </dl>
    <div class="bird-result__confidence" data-testid="bird-result-confidence">
      <div class="bird-result__confidence-head">
        <span>置信度</span>
        <strong>${percentText(confidence)}</strong>
      </div>
      <div class="bird-result__confidence-bar"><span style="width:${confidence === null ? 0 : Math.round(confidence * 100)}%"></span></div>
    </div>
    ${
      result.note
        ? `<p class="bird-result__note">${icon('sparkles')}<span>${escapeHtml(result.note)}</span></p>`
        : ''
    }
    ${alternativesHtml(result.alternatives)}
    <p class="bird-result__model">模型：${escapeHtml(String(task.model ?? '').trim() || '—')}${
      result.referenceSource ? ` · 参考来源 ${escapeHtml(result.referenceSource)}` : ''
    }</p>
  `;
  return info;
}

function buildFailedBody(task: BirdTask): HTMLElement {
  const root = document.createElement('div');
  root.className = 'bird-result bird-result--message';
  const message = String(task.error?.message ?? '').trim() || '识别失败，请检查网络与 AI 配置后重试。';
  const code = String(task.error?.code ?? '').trim();
  root.innerHTML = `
    <div class="bird-result__error" data-testid="bird-result-error">
      <div class="bird-result__error-head">${icon('alert')}<span>识别失败</span></div>
      ${code ? `<p class="bird-result__error-code">${escapeHtml(code)}</p>` : ''}
      <p class="bird-result__error-message">${escapeHtml(message)}</p>
    </div>
    <p class="bird-result__model">照片：${escapeHtml(task.photoName)} · 模型：${escapeHtml(String(task.model ?? '').trim() || '—')}</p>
    <p class="bird-result__note">${icon('info')}<span>「重新识别」会为同一张照片新建任务；失败的旧任务会保留在队列历史中。</span></p>
  `;
  return root;
}

function buildProgressBody(task: BirdTask): HTMLElement {
  const root = document.createElement('div');
  root.className = 'bird-result bird-result--message';
  const percent = Math.round(Math.min(100, Math.max(0, Number.isFinite(task.progress) ? task.progress : 0)));
  const statusText = task.status === 'queued' ? '排队中，等待识别' : 'AI 正在识别这张照片…';
  root.innerHTML = `
    <div class="bird-result__progress">
      <span class="spinner spinner--lg" aria-hidden="true"></span>
      <p class="bird-result__progress-title">${escapeHtml(statusText)}</p>
      <div class="bird-result__confidence-bar"><span style="width:${percent}%"></span></div>
      <p class="bird-result__progress-meta">${percent}% · 照片 ${escapeHtml(task.photoName)}</p>
    </div>
    <p class="bird-result__note">${icon('info')}<span>任务在后台运行，可以关闭窗口继续选片；完成后从队列点开查看并确认。</span></p>
  `;
  return root;
}

function buildActions(options: BirdResultModalOptions): ModalAction[] {
  const { task, sessionId } = options;
  const actions: ModalAction[] = [];

  if (task.status === 'completed' && task.result?.identified) {
    actions.push({
      label: '采纳并写入 EXIF',
      kind: 'primary',
      testId: 'bird-adopt-button',
      onClick: async () => {
        try {
          const response = await api.adoptBirdTask(sessionId, task.id);
          const observation = response.observation;
          if (response.exif?.written) {
            showToast(
              `已采纳：${observation.species}（结果 JSON 已记录，照片 EXIF 已写入）`,
              'success',
              5600,
            );
          } else {
            showToast(
              `已采纳并记录到结果 JSON，但照片 EXIF 写入失败：${
                response.exif?.error || '未知原因'
              }`,
              'warn',
              6800,
            );
          }
          options.onAdopted?.(response);
          return undefined;
        } catch (error) {
          showToast(errorText(error), 'error', 5200);
          return false;
        }
      },
    });
  }

  if (task.status === 'failed' && options.onRetry) {
    actions.push({
      label: '重新识别',
      kind: 'primary',
      testId: 'bird-retry-button',
      onClick: () => {
        options.onRetry?.();
      },
    });
  }

  if (task.status === 'completed' && task.result?.identified) {
    actions.push({
      label: '忽略',
      kind: 'ghost',
      testId: 'bird-dismiss-button',
      onClick: async () => {
        try {
          const updated = await api.dismissBirdTask(sessionId, task.id);
          showToast('已忽略该识别任务', 'info', 2600);
          options.onDismissed?.(updated);
          return undefined;
        } catch (error) {
          showToast(errorText(error), 'error', 5200);
          return false;
        }
      },
    });
  } else if (task.status === 'failed' || (task.status === 'completed' && !task.result?.identified)) {
    actions.push({
      label: '忽略',
      kind: 'ghost',
      testId: 'bird-dismiss-button',
      onClick: async () => {
        try {
          const updated = await api.dismissBirdTask(sessionId, task.id);
          showToast('已忽略该识别任务', 'info', 2600);
          options.onDismissed?.(updated);
          return undefined;
        } catch (error) {
          showToast(errorText(error), 'error', 5200);
          return false;
        }
      },
    });
  }

  if (!actions.length) actions.push({ label: '关闭', kind: 'ghost' });
  return actions;
}

/** 打开 AI 识鸟任务模态框（结果 / 失败 / 进度 / 回看）。 */
export function openBirdResultModal(options: BirdResultModalOptions): ModalHandle {
  const { task } = options;
  let body: HTMLElement;
  if (task.status === 'failed') {
    body = document.createElement('div');
    body.className = 'bird-result bird-result--with-compare';
    const compare = buildCompare(task, options.sessionId);
    compare.classList.add('bird-result__compare--single');
    body.appendChild(compare);
    body.appendChild(buildFailedBody(task));
  } else if (task.status === 'queued' || task.status === 'running') {
    body = document.createElement('div');
    body.className = 'bird-result bird-result--with-compare';
    const compare = buildCompare(task, options.sessionId);
    compare.classList.add('bird-result__compare--single');
    body.appendChild(compare);
    body.appendChild(buildProgressBody(task));
  } else {
    body = document.createElement('div');
    body.className = 'bird-result bird-result--with-compare';
    body.appendChild(buildCompare(task, options.sessionId));
    body.appendChild(buildResultInfo(task));
  }

  return openModal({
    title: 'AI 识鸟结果',
    subtitle: `${task.photoName} · ${task.model || 'doubao-seed-2-1-lite'}`,
    size: 'lg',
    testId: 'bird-result-modal',
    body,
    actions: buildActions(options),
  });
}
