/**
 * AI 识鸟设置弹窗（docs/BIRDID-CONTRACT.md §8.4）。
 *
 * 入口：启动页底部「AI 识鸟设置」按钮 + 选片页顶部齿轮图标按钮（同一 testid，两处都可用）。
 * - API Key 密码框：已配置时占位显示掩码，留空 = 不修改；
 * - 高级（折叠）：Base URL / 模型名；
 * - 状态行展示已配置 / 未配置 / 模拟模式 / Key 来源；
 * - 「测试连接」调用 POST /api/bird/verify，「保存」调用 PUT /api/bird/config。
 */
import * as api from '../../api';
import { errorText } from '../../api';
import type { BirdConfigRequest, BirdStatus } from '../../types';
import { escapeHtml, icon, qs } from '../dom';
import { openModal, type ModalHandle } from '../modal';
import { showToast } from '../toast';

export interface BirdSettingsModalOptions {
  /** 保存成功后回传最新状态（选片页据此更新「是否已配置」缓存）。 */
  onSaved?: (status: BirdStatus) => void;
}

function keySourceLabel(source: BirdStatus['keySource']): string {
  switch (source) {
    case 'env':
      return '环境变量';
    case 'config':
      return '本机配置文件';
    case 'mock':
      return '模拟模式';
    default:
      return '';
  }
}

function describeStatus(status: BirdStatus): string {
  if (status.mock) {
    return `模拟模式 · 不调用真实接口 · 模型 ${escapeHtml(status.model || '—')}`;
  }
  if (!status.configured) return '未配置 · 请填写 API Key 后保存';
  const source = keySourceLabel(status.keySource);
  const masked = status.keyMasked ? `（Key ${escapeHtml(status.keyMasked)}）` : '';
  const protocol =
    status.endpointProtocol === 'responses'
      ? 'Responses API'
      : status.endpointProtocol === 'chat'
        ? 'Chat Completions'
        : '端点无效';
  return `已配置 · 模型 ${escapeHtml(status.model || '—')} · 接口 ${protocol}${source ? ` · Key 来源 ${source}` : ''}${masked}`;
}

/** 打开 AI 识鸟设置弹窗。 */
export function openBirdSettingsModal(options: BirdSettingsModalOptions = {}): ModalHandle {
  const body = document.createElement('div');
  body.className = 'bird-settings';
  body.innerHTML = `
    <p class="bird-settings__lead">
      AI 识鸟调用火山方舟多模态模型 <strong>doubao-seed-2-1-lite</strong> 识别照片中的鸟（科 / 属 / 种 + 参考照片）。
      API Key 只保存在本机配置文件，接口响应不会返回明文 Key。
    </p>
    <div class="bird-settings__notice">
      ${icon('alert')}
      <span>识别时<strong>照片会上传到火山方舟服务</strong>；请只在可信的本机环境填写 Key。</span>
    </div>
    <label class="bird-field">
      <span class="bird-field__label">
        <span>API Key</span>
        <span class="bird-field__hint" data-role="key-hint">留空 = 不修改</span>
      </span>
      <input
        type="password"
        class="bird-input"
        data-testid="bird-api-key-input"
        placeholder="ark-…"
        autocomplete="off"
        spellcheck="false"
      />
    </label>
    <details class="bird-advanced">
      <summary>${icon('settings')}<span>高级设置（Base URL / 模型名）</span></summary>
      <div class="bird-advanced__body">
        <label class="bird-field">
          <span class="bird-field__label"><span>API 端点（完整 URL）</span></span>
          <input
            type="text"
            class="bird-input"
            data-testid="bird-endpoint-input"
            placeholder="https://ark.cn-beijing.volces.com/api/v3/chat/completions"
            autocomplete="off"
            spellcheck="false"
          />
        </label>
        <label class="bird-field">
          <span class="bird-field__label"><span>模型名</span></span>
          <input
            type="text"
            class="bird-input"
            data-testid="bird-model-input"
            placeholder="doubao-seed-2-1-lite"
            autocomplete="off"
            spellcheck="false"
          />
        </label>
        <p class="bird-field__help">端点填<strong>完整接口 URL</strong>，后端原样请求、不拼接：<code>https://ark.cn-beijing.volces.com/api/v3/chat/completions</code>（Chat Completions）或 <code>https://ark.cn-beijing.volces.com/api/v3/responses</code>（Responses API）。模型名可填火山方舟控制台的模型名或 endpoint id；环境变量优先级高于此处设置。</p>
      </div>
    </details>
    <p class="bird-config-status" data-testid="bird-config-status" data-role="status">
      ${icon('info')}<span>正在读取配置…</span>
    </p>
  `;

  const keyInput = qs<HTMLInputElement>(body, '[data-testid="bird-api-key-input"]');
  const endpointInput = qs<HTMLInputElement>(body, '[data-testid="bird-endpoint-input"]');
  const modelInput = qs<HTMLInputElement>(body, '[data-testid="bird-model-input"]');
  const statusEl = qs<HTMLElement>(body, '[data-testid="bird-config-status"]');
  const keyHint = qs<HTMLElement>(body, '[data-role="key-hint"]');

  const setStatus = (html: string, tone: 'ok' | 'warn' | 'muted'): void => {
    const toneClass = tone === 'muted' ? '' : ` is-${tone}`;
    const iconName = tone === 'ok' ? 'check' : tone === 'warn' ? 'alert' : 'info';
    statusEl.className = `bird-config-status${toneClass}`;
    statusEl.innerHTML = `${icon(iconName)}<span>${html}</span>`;
  };

  let currentStatus: BirdStatus | null = null;
  void (async () => {
    try {
      const status = await api.getBirdStatus();
      currentStatus = status;
      keyInput.placeholder = status.keyMasked ? `已配置：${status.keyMasked}（留空不修改）` : 'ark-…';
      keyHint.textContent = status.configured ? '已配置，留空 = 不修改' : '尚未配置';
      endpointInput.value = status.endpoint ?? '';
      modelInput.value = status.model ?? '';
      const warning = status.endpointWarning ? ` · ${status.endpointWarning}` : '';
      setStatus(
        describeStatus(status) + escapeHtml(warning),
        status.endpointWarning ? 'warn' : status.configured || status.mock ? 'ok' : 'warn',
      );
    } catch (error) {
      keyHint.textContent = '读取配置失败，可继续填写后保存';
      endpointInput.placeholder = 'https://ark.cn-beijing.volces.com/api/v3/chat/completions';
      modelInput.placeholder = 'doubao-seed-2-1-lite';
      setStatus(`无法读取当前配置：${escapeHtml(errorText(error))}`, 'warn');
    }
  })();

  const modal = openModal({
    title: 'AI 识鸟设置',
    subtitle: '火山方舟 · doubao-seed-2-1-lite',
    size: 'md',
    testId: 'bird-settings-modal',
    body,
    actions: [
      {
        label: '测试连接',
        kind: 'ghost',
        testId: 'bird-config-verify',
        onClick: async () => {
          try {
            const result = await api.verifyBirdConfig();
            showToast(
              result.message || (result.ok ? '连接成功' : '连接失败'),
              result.ok ? 'success' : 'error',
              5600,
            );
          } catch (error) {
            showToast(`测试连接失败：${errorText(error)}`, 'error', 5600);
          }
          // 保持弹窗打开，方便根据结果调整 Key / 模型名。
          return false;
        },
      },
      {
        label: '保存',
        kind: 'primary',
        testId: 'bird-config-save',
        onClick: async () => {
          const payload: BirdConfigRequest = {};
          const apiKey = keyInput.value.trim();
          if (apiKey) payload.apiKey = apiKey;
          const endpoint = endpointInput.value.trim();
          const model = modelInput.value.trim();
          // 读到了当前配置：只在用户改动时提交（清空 = 清除该字段）；
          // 读不到配置时只提交非空值，避免误清空已有设置。
          if (currentStatus) {
            if (endpoint !== (currentStatus.endpoint ?? '')) payload.endpoint = endpoint;
            if (model !== (currentStatus.model ?? '')) payload.model = model;
          } else {
            if (endpoint) payload.endpoint = endpoint;
            if (model) payload.model = model;
          }
          try {
            const saved = await api.updateBirdConfig(payload);
            showToast('AI 识鸟配置已保存', 'success', 3200);
            options.onSaved?.(saved);
            return undefined;
          } catch (error) {
            showToast(errorText(error), 'error', 5600);
            return false;
          }
        },
      },
    ],
  });

  // 打开后把焦点放在 Key 输入框（已配置时也可直接覆盖）。
  keyInput.focus({ preventScroll: true });

  return modal;
}
