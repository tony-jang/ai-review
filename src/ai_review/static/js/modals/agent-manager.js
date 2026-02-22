// Agent Manager Modal

import { STRICTNESS_OPTIONS, AGENT_FIELD_HELP, MODEL_DEFAULT_HINTS, AGENT_PRESETS, providerIconSvg } from '../constants.js';
import { esc, _escapeAttr, getModelColor } from '../utils.js';
import state from '../state.js';

// Module-local state
let _amAvailableModels = {};
let _amSelectedAgentId = null;
let _amMode = 'list'; // 'list' | 'edit' | 'add'
let _amOverlay = null;
let _amPresetCache = [];
let _amDirty = false;

function _amSetDirty(dirty) {
  _amDirty = !!dirty;
  const badge = document.getElementById('am-dirty-indicator');
  if (!badge) return;
  badge.style.display = _amDirty ? '' : 'none';
}

function _amBindDirtyTracking() {
  const right = _amOverlay?.querySelector('#am-right');
  if (!right) return;
  right.querySelectorAll('input, textarea, select').forEach((el) => {
    el.addEventListener('input', () => _amSetDirty(true));
    el.addEventListener('change', () => _amSetDirty(true));
  });
  _amSetDirty(false);
}

function _amConfirmDiscardChanges(actionText) {
  if (!_amDirty) return true;
  const action = actionText || '이동';
  return confirm(`저장되지 않은 변경 사항이 있습니다. 저장하지 않고 ${action}하시겠습니까?`);
}

export function _amRequestClose() {
  if (!_amConfirmDiscardChanges('창을 닫기')) return;
  _amOverlay?.remove();
  _amOverlay = null;
  _amSelectedAgentId = null;
  _amMode = 'list';
  _amSetDirty(false);
}

function _amLabel(text, helpText) {
  const help = (helpText || '').trim();
  const titleAttr = help ? ` title="${esc(help)}"` : '';
  return `<label style="font-size:11px;color:var(--text-muted);margin-bottom:4px;display:flex;align-items:center;gap:6px"${titleAttr}><span>${esc(text)}</span>${help ? `<span class="help-dot" title="${esc(help)}">?</span>` : ''}</label>`;
}

function _amModelHintByClientType(clientType) {
  return MODEL_DEFAULT_HINTS[clientType] || '기본값(빈 값): 클라이언트 기본 모델 사용';
}

function _amRefreshModelHint(mode) {
  const prefix = mode === 'new' ? 'am-new' : 'am';
  const ct = document.getElementById(`${prefix}-client-type`)?.value || 'claude-code';
  const hintEl = document.getElementById(`${prefix}-model-default-hint`);
  if (hintEl) hintEl.textContent = _amModelHintByClientType(ct);
}

async function _amFetchAvailableModels() {
  try {
    const r = await fetch('/api/available-models');
    if (r.ok) _amAvailableModels = await r.json();
  } catch (e) { console.warn('available-models fetch failed', e); }
}

function _amRefreshConnectionHint(mode) {
  const prefix = mode === 'new' ? 'am-new' : 'am';
  const targetEl = document.getElementById(`${prefix}-test-target`);
  if (!targetEl) return;
  targetEl.textContent = '실행 시 콜백 URL/세션 ID를 자동 생성해 에이전트에게 전달합니다.';
}

export async function openAgentManager(editModelId) {
  await _amFetchAvailableModels();

  let presets = [];
  try {
    const r = await fetch('/api/agent-presets');
    if (r.ok) presets = await r.json();
  } catch (e) {}

  _amOverlay = document.createElement('div');
  _amOverlay.className = 'report-overlay';
  _amOverlay.innerHTML = `<div class="report-card" style="max-width:900px;height:80vh;display:flex;flex-direction:column;overflow:hidden">
    <div class="report-header"><h2>에이전트 프리셋 관리</h2><button class="report-close" onclick="_amRequestClose()">✕</button></div>
    <div style="display:flex;flex:1;overflow:hidden">
      <div id="am-left" style="width:240px;min-width:200px;border-right:1px solid var(--border);display:flex;flex-direction:column;overflow:hidden">
        <div id="am-agent-list" style="flex:1;overflow-y:auto;padding:8px 0"></div>
        <div style="padding:8px 12px;border-top:1px solid var(--border)">
          <button class="btn btn-primary" onclick="_amShowAddPanel()" style="width:100%;font-size:12px">+ 프리셋 추가</button>
        </div>
      </div>
      <div id="am-right" style="flex:1;overflow-y:auto;padding:20px 20px 0">
        <div style="color:var(--text-muted);font-size:13px;text-align:center;margin-top:40px">좌측에서 프리셋을 선택하거나 추가하세요</div>
      </div>
    </div>
  </div>`;
  document.body.appendChild(_amOverlay);
  _amOverlay.addEventListener('click', (e) => { if (e.target === _amOverlay) _amRequestClose(); });
  _amSetDirty(false);

  if (editModelId) {
    _amSelectedAgentId = editModelId;
    _amMode = 'edit';
  }

  _amRenderList(presets);

  if (editModelId) {
    _amRenderEditForm(presets.find(a => a.id === editModelId), presets);
  }
}

function _amRenderList(agents) {
  _amPresetCache = agents || [];
  const listEl = _amOverlay?.querySelector('#am-agent-list');
  if (!listEl) return;
  if (!agents.length) {
    listEl.innerHTML = '<div style="padding:20px;text-align:center;color:var(--text-muted);font-size:12px">프리셋이 없습니다</div>';
    return;
  }
  listEl.innerHTML = agents.map(a => {
    const color = a.color || '#8B949E';
    const sel = _amSelectedAgentId === a.id;
    return `<div onclick="_amSelectAgent('${esc(a.id)}')" style="padding:8px 12px;cursor:pointer;display:flex;align-items:center;gap:8px;font-size:12px;${sel ? 'background:rgba(88,166,255,0.1);border-left:3px solid var(--accent)' : 'border-left:3px solid transparent'};transition:background 0.15s" onmouseover="this.style.background='rgba(88,166,255,0.06)'" onmouseout="this.style.background='${sel ? 'rgba(88,166,255,0.1)' : 'transparent'}'">
      <span style="width:10px;height:10px;border-radius:50%;background:${color};flex-shrink:0;opacity:${a.enabled !== false ? '1' : '0.3'}"></span>
      <div style="flex:1;min-width:0">
        <div style="font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;${a.enabled === false ? 'opacity:0.4' : ''}">${esc(a.id)}</div>
        <div style="color:var(--text-muted);font-size:11px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(a.description || a.client_type || '')}</div>
      </div>
      <button onclick="event.stopPropagation();_amDeleteAgent('${esc(a.id)}')" title="삭제" style="background:none;border:none;color:var(--severity-critical);cursor:pointer;font-size:14px;padding:2px 4px;opacity:0.5;flex-shrink:0" onmouseover="this.style.opacity='1'" onmouseout="this.style.opacity='0.5'">✕</button>
    </div>`;
  }).join('');
}

export async function _amSelectAgent(modelId) {
  if (_amSelectedAgentId !== modelId && !_amConfirmDiscardChanges('다른 프리셋으로 이동')) return;
  _amSelectedAgentId = modelId;
  _amMode = 'edit';
  let agents = [];
  try {
    const r = await fetch('/api/agent-presets');
    if (r.ok) agents = await r.json();
  } catch (e) {}
  _amRenderList(agents);
  const mc = agents.find(a => a.id === modelId);
  if (mc) _amRenderEditForm(mc, agents);
}

function _amRenderEditForm(mc, agents) {
  const right = _amOverlay?.querySelector('#am-right');
  if (!right || !mc) return;
  const inputStyle = 'background:var(--bg);color:var(--text);border:1px solid var(--border);border-radius:6px;padding:8px 12px;font-size:13px;font-family:inherit;width:100%';
  const focus = (mc.review_focus || []).join(', ');
  const strictness = mc.strictness || 'balanced';
  const clientType = mc.client_type || 'claude-code';
  const models = _amAvailableModels[clientType] || [];

  right.innerHTML = `
    <div style="display:flex;flex-direction:column;gap:14px">
      <div style="display:flex;align-items:center;gap:8px;margin-bottom:4px">
        <span style="width:14px;height:14px;border-radius:50%;background:${mc.color || '#8B949E'}"></span>
        <input id="am-id" value="${esc(mc.id)}" style="font-size:16px;font-weight:600;background:transparent;color:var(--text);border:1px solid transparent;border-radius:4px;padding:2px 6px;outline:none;max-width:200px" onfocus="this.style.borderColor='var(--accent)'" onblur="this.style.borderColor='transparent'">
        <span style="font-size:12px;color:var(--text-muted)">${esc(mc.client_type || '')}</span>
        <span id="am-dirty-indicator" class="am-dirty-indicator" style="display:none;margin-left:auto">저장 안 됨</span>
      </div>

      <div>
        ${_amLabel('설명', AGENT_FIELD_HELP.description)}
        <input id="am-description" value="${esc(mc.description || '')}" style="${inputStyle}" placeholder="에이전트 설명">
      </div>

      <div>
        ${_amLabel('엄격도', AGENT_FIELD_HELP.strictness)}
        <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:8px">
          ${STRICTNESS_OPTIONS.map(so => `<label title="${esc(so.desc)}" onclick="const el=document.getElementById('am-strictness-${so.value}'); if(el){ el.checked=true; el.dispatchEvent(new Event('change', { bubbles:true })); }" style="display:flex;flex-direction:column;gap:4px;padding:10px;border:1px solid ${strictness === so.value ? 'var(--accent)' : 'var(--border)'};border-radius:8px;cursor:pointer;background:${strictness === so.value ? 'rgba(88,166,255,0.08)' : 'var(--bg)'};transition:all 0.15s">
            <div style="display:flex;align-items:center;gap:6px">
              <input type="radio" name="am-strictness" id="am-strictness-${so.value}" value="${so.value}" ${strictness === so.value ? 'checked' : ''} onchange="_amUpdateStrictnessUI()" style="accent-color:var(--accent)">
              <span style="font-size:13px;font-weight:600">${so.label}</span>
            </div>
            <span style="font-size:11px;color:var(--text-muted)">${so.desc}</span>
          </label>`).join('')}
        </div>
      </div>

      <div style="display:flex;gap:8px">
        <div style="flex:1">
          ${_amLabel('클라이언트 타입', AGENT_FIELD_HELP.client_type)}
          <div class="client-type-select-wrap">
            <span id="am-client-type-icon" class="client-type-icon">${providerIconSvg(clientType, 16)}</span>
            <select id="am-client-type" style="${inputStyle};padding-left:32px" onchange="_amOnClientTypeChange()">
              ${['claude-code','codex','opencode','gemini'].map(t => `<option value="${t}" ${clientType === t ? 'selected' : ''}>${t}</option>`).join('')}
            </select>
          </div>
        </div>
        <div style="flex:1">
          ${_amLabel('세부 모델', AGENT_FIELD_HELP.model_id)}
          <select id="am-model-id-select" style="${inputStyle}" onchange="document.getElementById('am-model-id').value=this.value">
            <option value="">기본값 (클라이언트 설정)</option>
            ${models.map(m => `<option value="${m.model_id}" ${mc.model_id === m.model_id ? 'selected' : ''}>${m.label}</option>`).join('')}
          </select>
          <input id="am-model-id" value="${esc(mc.model_id || '')}" placeholder="또는 직접 입력" style="${inputStyle};margin-top:4px;font-size:11px">
          <div id="am-model-default-hint" class="field-hint"></div>
          <div class="field-hint">프리셋은 일부만 노출됩니다. 필요한 모델은 직접 입력하세요.</div>
        </div>
      </div>

      <div id="am-provider-row" style="${clientType === 'opencode' ? '' : 'display:none'}">
        ${_amLabel('Provider', AGENT_FIELD_HELP.provider)}
        <input id="am-provider" value="${esc(mc.provider || '')}" style="${inputStyle}" placeholder="예: openai">
      </div>

      <div>
        ${_amLabel('색상', '에이전트 카드/표시에 사용됩니다.')}
        <div style="display:flex;gap:8px;align-items:center">
          <input id="am-color" type="color" value="${mc.color || '#8B949E'}" style="width:40px;height:32px;border:1px solid var(--border);border-radius:6px;background:none;cursor:pointer" oninput="document.getElementById('am-color-text').value=this.value">
          <input id="am-color-text" value="${esc(mc.color || '')}" placeholder="#8B5CF6" style="flex:1;${inputStyle}" oninput="if(/^#[0-9a-fA-F]{6}$/.test(this.value))document.getElementById('am-color').value=this.value">
        </div>
      </div>

      <div>
        ${_amLabel('시스템 프롬프트', AGENT_FIELD_HELP.system_prompt)}
        <textarea id="am-system-prompt" rows="3" style="${inputStyle};resize:vertical">${esc(mc.system_prompt || '')}</textarea>
      </div>

      <div style="display:flex;gap:8px">
        <div style="flex:1">
          ${_amLabel('Temperature', AGENT_FIELD_HELP.temperature)}
          <input id="am-temperature" type="number" step="0.1" min="0" max="2" value="${mc.temperature != null ? mc.temperature : ''}" placeholder="기본값" style="${inputStyle}">
          <div class="field-hint">낮을수록 일관되고, 높을수록 다양한 답변이 나옵니다.</div>
        </div>
        <div style="flex:2">
          ${_amLabel('리뷰 포커스 (쉼표 구분)', AGENT_FIELD_HELP.review_focus)}
          <input id="am-review-focus" value="${esc(focus)}" placeholder="security, performance, ..." style="${inputStyle}">
        </div>
      </div>

      <div class="am-action-footer">
        <div style="display:flex;align-items:center;gap:8px">
          <button class="btn" type="button" onclick="_amTestConnection('edit', this)">연결 테스트</button>
          <span id="am-test-status" class="conn-test-status"></span>
        </div>
        <div style="display:flex;align-items:center;gap:8px">
          <label style="display:flex;align-items:center;gap:6px;font-size:13px;cursor:pointer">
            <input type="checkbox" id="am-enabled" ${mc.enabled !== false ? 'checked' : ''} style="accent-color:var(--accent)">
            활성화
          </label>
          <button class="btn btn-primary" onclick="_amSaveAgent('${esc(mc.id)}')" style="padding:8px 24px">저장</button>
        </div>
      </div>
    </div>`;
  _amUpdateStrictnessUI();
  _amRefreshModelHint('edit');
  _amRefreshConnectionHint('edit');
  _amBindDirtyTracking();
}

export function _amUpdateStrictnessUI() {
  const checked = document.querySelector('input[name="am-strictness"]:checked');
  if (!checked) return;
  STRICTNESS_OPTIONS.forEach(so => {
    const label = document.getElementById('am-strictness-' + so.value)?.closest('label');
    if (!label) return;
    const sel = so.value === checked.value;
    label.style.borderColor = sel ? 'var(--accent)' : 'var(--border)';
    label.style.background = sel ? 'rgba(88,166,255,0.08)' : 'var(--bg)';
  });
}

export function _amOnClientTypeChange() {
  const ct = document.getElementById('am-client-type')?.value || 'claude-code';
  const providerRow = document.getElementById('am-provider-row');
  if (providerRow) providerRow.style.display = ct === 'opencode' ? '' : 'none';
  const iconEl = document.getElementById('am-client-type-icon');
  if (iconEl) iconEl.innerHTML = providerIconSvg(ct, 16);
  // Update model dropdown
  const modelSelect = document.getElementById('am-model-id-select');
  if (modelSelect) {
    const models = _amAvailableModels[ct] || [];
    modelSelect.innerHTML = '<option value="">기본값 (클라이언트 설정)</option>' + models.map(m => `<option value="${m.model_id}">${m.label}</option>`).join('');
  }
  _amRefreshModelHint('edit');
  _amRefreshConnectionHint('edit');
}

export async function _amSaveAgent(modelId) {
  const newId = (document.getElementById('am-id')?.value || '').trim();
  const tempVal = (document.getElementById('am-temperature')?.value || '').trim();
  const focusVal = (document.getElementById('am-review-focus')?.value || '').trim();
  const strictnessEl = document.querySelector('input[name="am-strictness"]:checked');
  const payload = {
    description: (document.getElementById('am-description')?.value || '').trim(),
    strictness: strictnessEl?.value || 'balanced',
    color: (document.getElementById('am-color-text')?.value || '').trim(),
    client_type: document.getElementById('am-client-type')?.value || 'claude-code',
    provider: (document.getElementById('am-provider')?.value || '').trim(),
    model_id: (document.getElementById('am-model-id')?.value || '').trim(),
    system_prompt: document.getElementById('am-system-prompt')?.value || '',
    temperature: tempVal ? parseFloat(tempVal) : null,
    review_focus: focusVal ? focusVal.split(',').map(s => s.trim()).filter(Boolean) : [],
    enabled: document.getElementById('am-enabled')?.checked ?? true,
  };
  try {
    // Rename if ID changed
    let effectiveId = modelId;
    if (newId && newId !== modelId) {
      const rr = await fetch(`/api/agent-presets/${modelId}/rename`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ new_id: newId }),
      });
      if (!rr.ok) { const e = await rr.json(); window.showToast(e.detail || '이름 변경 실패', 'error'); return; }
      effectiveId = newId;
    }
    const r = await fetch(`/api/agent-presets/${effectiveId}`, {
      method: 'PUT',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload),
    });
    if (!r.ok) { const e = await r.json(); window.showToast(e.detail || '저장 실패', 'error'); alert(e.detail || '저장 실패'); return; }
    _amSetDirty(false);
    _amSelectedAgentId = effectiveId;
    window.showToast('프리셋이 저장되었습니다.', 'success');
    window.dispatchEvent(new CustomEvent('agent-presets-updated'));
    // Re-fetch and re-render
    let agents = [];
    try { const r2 = await fetch('/api/agent-presets'); if (r2.ok) agents = await r2.json(); } catch (e) {}
    _amRenderList(agents);
    const mc = agents.find(a => a.id === effectiveId);
    if (mc) _amRenderEditForm(mc, agents);
  } catch (e) { window.showToast('저장 실패', 'error'); alert('저장 실패'); }
}

export async function _amDeleteAgent(modelId) {
  if (!confirm(`${modelId} 프리셋을 삭제하시겠습니까?`)) return;
  try {
    const r = await fetch(`/api/agent-presets/${modelId}`, { method: 'DELETE' });
    if (!r.ok) { const e = await r.json(); alert(e.detail || '삭제 실패'); return; }
    window.dispatchEvent(new CustomEvent('agent-presets-updated'));
    // Re-fetch and re-render
    let agents = [];
    try { const r2 = await fetch('/api/agent-presets'); if (r2.ok) agents = await r2.json(); } catch (e) {}
    _amSelectedAgentId = null;
    _amRenderList(agents);
    const right = _amOverlay?.querySelector('#am-right');
    if (right) right.innerHTML = '<div style="color:var(--text-muted);font-size:13px;text-align:center;margin-top:40px">좌측에서 프리셋을 선택하거나 추가하세요</div>';
    _amSetDirty(false);
    window.showToast('프리셋이 삭제되었습니다.', 'success');
  } catch (e) { alert('삭제 실패'); }
}

function _amDismissPopover() {
  _amOverlay?.querySelector('.preset-catalog-popover')?.remove();
}

export function _amShowAddPanel(force = false) {
  // Toggle: if popover already open, close it
  const existing = _amOverlay?.querySelector('.preset-catalog-popover');
  if (existing) { existing.remove(); return; }

  if (!force && !_amConfirmDiscardChanges('프리셋 추가 화면으로 이동')) return;

  // Group presets by group field
  const groups = [];
  const groupMap = {};
  AGENT_PRESETS.forEach((p, i) => {
    const g = p.group || 'Other';
    if (!groupMap[g]) { groupMap[g] = { name: g, clientType: p.client_type, items: [] }; groups.push(groupMap[g]); }
    groupMap[g].items.push({ preset: p, flatIndex: i });
  });

  const popover = document.createElement('div');
  popover.className = 'preset-catalog-popover';
  popover.innerHTML = `
    ${groups.map(g => `
      <div class="preset-catalog-group">
        <div class="preset-catalog-header"><span class="preset-catalog-icon">${providerIconSvg(g.clientType, 14)}</span> ${esc(g.name)}</div>
        ${g.items.map(({ preset: p, flatIndex: fi }) => `
          <div class="preset-catalog-item" data-preset-index="${fi}">
            <span class="preset-catalog-icon">${providerIconSvg(p.client_type, 16)}</span>
            <span class="preset-catalog-color" style="background:${p.color}"></span>
            <span>${esc(p.label)}</span>
            <span class="preset-catalog-model">${esc(p.model_id || '')}</span>
          </div>`).join('')}
      </div>`).join('')}
    <div class="preset-catalog-group">
      <div class="preset-catalog-item preset-catalog-custom" data-preset-index="-1">
        <span class="preset-catalog-icon"><svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor" class="provider-icon"><path d="M19.14 12.94a7.07 7.07 0 0 0 .06-.94 7.07 7.07 0 0 0-.06-.94l2.03-1.58a.49.49 0 0 0 .12-.61l-1.92-3.32a.49.49 0 0 0-.59-.22l-2.39.96a7.04 7.04 0 0 0-1.62-.94l-.36-2.54a.48.48 0 0 0-.48-.41h-3.84a.48.48 0 0 0-.48.41l-.36 2.54a7.04 7.04 0 0 0-1.62.94l-2.39-.96a.49.49 0 0 0-.59.22L2.74 8.87a.48.48 0 0 0 .12.61l2.03 1.58a7.07 7.07 0 0 0-.06.94c0 .31.02.63.06.94L2.86 14.52a.49.49 0 0 0-.12.61l1.92 3.32c.12.22.37.29.59.22l2.39-.96c.5.38 1.04.7 1.62.94l.36 2.54c.05.24.24.41.48.41h3.84c.24 0 .44-.17.48-.41l.36-2.54a7.04 7.04 0 0 0 1.62-.94l2.39.96c.22.08.47 0 .59-.22l1.92-3.32a.49.49 0 0 0-.12-.61l-2.03-1.58zM12 15.6A3.6 3.6 0 1 1 12 8.4a3.6 3.6 0 0 1 0 7.2z"/></svg></span>
        <span>커스텀</span>
        <span class="preset-catalog-model">직접 설정</span>
      </div>
    </div>`;

  popover.addEventListener('click', (e) => {
    const item = e.target.closest('.preset-catalog-item');
    if (!item) return;
    const idx = parseInt(item.dataset.presetIndex, 10);
    _amDismissPopover();
    _amBeginAddPreset(idx);
  });

  // Attach to .report-card and position relative to the button
  const card = _amOverlay?.querySelector('.report-card');
  const btn = _amOverlay?.querySelector('[onclick*="_amShowAddPanel"]');
  if (card && btn) {
    card.style.position = 'relative';
    const cardRect = card.getBoundingClientRect();
    const btnRect = btn.getBoundingClientRect();
    popover.style.left = `${btnRect.left - cardRect.left - 4}px`;
    popover.style.bottom = `${cardRect.bottom - btnRect.top + 6}px`;
    card.appendChild(popover);
  }

  // Close popover when clicking outside
  const onClickOutside = (e) => {
    if (!popover.contains(e.target) && !e.target.closest('[onclick*="_amShowAddPanel"]')) {
      _amDismissPopover();
      document.removeEventListener('click', onClickOutside, true);
    }
  };
  setTimeout(() => document.addEventListener('click', onClickOutside, true), 0);
}

function _amBeginAddPreset(presetIdx) {
  _amSelectedAgentId = null;
  _amMode = 'add';
  // De-select visually in left list
  try {
    const listEl = _amOverlay?.querySelector('#am-agent-list');
    if (listEl) {
      listEl.querySelectorAll('[onclick^="_amSelectAgent"]').forEach(el => {
        el.style.background = 'transparent';
        el.style.borderLeft = '3px solid transparent';
      });
    }
  } catch (e) {}

  const right = _amOverlay?.querySelector('#am-right');
  if (!right) return;
  right.innerHTML = `
    <div style="display:flex;align-items:center;gap:8px;margin-bottom:16px">
      <h3 style="font-size:14px;font-weight:600">프리셋 추가</h3>
      <span id="am-dirty-indicator" class="am-dirty-indicator" style="display:none">저장 안 됨</span>
    </div>
    <div id="am-add-form"></div>`;
  _amSetDirty(false);
  _amSelectAddPreset(presetIdx);
}

export function _amSelectAddPreset(presetIdx) {
  const isCustom = presetIdx === -1;
  const preset = isCustom ? null : AGENT_PRESETS[presetIdx];
  const inputStyle = 'background:var(--bg);color:var(--text);border:1px solid var(--border);border-radius:6px;padding:8px 12px;font-size:13px;font-family:inherit;width:100%';
  const defaultId = preset ? window.getUniqueAgentId(preset.id) : '';
  const defaultColor = preset ? preset.color : '#8B5CF6';
  const defaultClientType = preset ? preset.client_type : 'claude-code';
  const defaultModelId = preset?.model_id || '';
  const defaultProvider = preset?.provider || '';
  const showProvider = defaultClientType === 'opencode' || isCustom;
  const models = _amAvailableModels[defaultClientType] || [];

  const form = _amOverlay?.querySelector('#am-add-form');
  if (!form) return;
  form.style.display = 'block';
  form.innerHTML = `
    <div style="display:flex;flex-direction:column;gap:12px">
      <div>
        ${_amLabel('에이전트 ID', '중복되지 않는 고유 ID입니다. 예: codex, gemini-sec')}
        <input id="am-new-id" value="${esc(defaultId)}" placeholder="id (예: codex2)" style="${inputStyle}">
      </div>

      <div>
        ${_amLabel('설명', AGENT_FIELD_HELP.description)}
        <input id="am-new-description" placeholder="에이전트 설명" style="${inputStyle}">
      </div>

      <div>
        ${_amLabel('엄격도', AGENT_FIELD_HELP.strictness)}
        <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:8px">
          ${STRICTNESS_OPTIONS.map(so => `<label title="${esc(so.desc)}" onclick="const el=document.getElementById('am-new-strictness-${so.value}'); if(el){ el.checked=true; el.dispatchEvent(new Event('change', { bubbles:true })); }" style="display:flex;flex-direction:column;gap:4px;padding:10px;border:1px solid ${so.value === 'balanced' ? 'var(--accent)' : 'var(--border)'};border-radius:8px;cursor:pointer;background:${so.value === 'balanced' ? 'rgba(88,166,255,0.08)' : 'var(--bg)'};transition:all 0.15s">
            <div style="display:flex;align-items:center;gap:6px">
              <input type="radio" name="am-new-strictness" id="am-new-strictness-${so.value}" value="${so.value}" ${so.value === 'balanced' ? 'checked' : ''} onchange="_amUpdateNewStrictnessUI()" style="accent-color:var(--accent)">
              <span style="font-size:13px;font-weight:600">${so.label}</span>
            </div>
            <span style="font-size:11px;color:var(--text-muted)">${so.desc}</span>
          </label>`).join('')}
        </div>
      </div>

      <div style="display:flex;gap:8px">
        <div style="flex:1">
          ${_amLabel('클라이언트 타입', AGENT_FIELD_HELP.client_type)}
          <div class="client-type-select-wrap">
            <span id="am-new-client-type-icon" class="client-type-icon">${providerIconSvg(defaultClientType, 16)}</span>
            <select id="am-new-client-type" style="${inputStyle};padding-left:32px" ${!isCustom ? 'disabled' : ''} onchange="_amOnNewClientTypeChange()">
              ${['claude-code','codex','opencode','gemini'].map(t => `<option value="${t}" ${defaultClientType === t ? 'selected' : ''}>${t}</option>`).join('')}
            </select>
          </div>
        </div>
        <div style="flex:1">
          ${_amLabel('세부 모델', AGENT_FIELD_HELP.model_id)}
          <select id="am-new-model-id-select" style="${inputStyle}" onchange="document.getElementById('am-new-model-id').value=this.value">
            <option value="">기본값 (클라이언트 설정)</option>
            ${models.map(m => `<option value="${m.model_id}" ${defaultModelId === m.model_id ? 'selected' : ''}>${m.label}</option>`).join('')}
          </select>
          <input id="am-new-model-id" value="${esc(defaultModelId)}" placeholder="또는 직접 입력" style="${inputStyle};margin-top:4px;font-size:11px">
          <div id="am-new-model-default-hint" class="field-hint"></div>
          <div class="field-hint">프리셋은 일부만 노출됩니다. 필요한 모델은 직접 입력하세요.</div>
        </div>
      </div>

      <div id="am-new-provider-row" style="${showProvider ? '' : 'display:none'}">
        ${_amLabel('Provider', AGENT_FIELD_HELP.provider)}
        <input id="am-new-provider" value="${esc(defaultProvider)}" placeholder="provider (예: openai)" style="${inputStyle}">
      </div>

      <div>
        ${_amLabel('색상', '에이전트 카드/표시에 사용됩니다.')}
        <div style="display:flex;gap:8px;align-items:center">
          <input id="am-new-color" type="color" value="${defaultColor}" style="width:40px;height:32px;border:1px solid var(--border);border-radius:6px;background:none;cursor:pointer" oninput="document.getElementById('am-new-color-text').value=this.value">
          <input id="am-new-color-text" value="${defaultColor}" placeholder="#8B5CF6" style="flex:1;${inputStyle}" oninput="if(/^#[0-9a-fA-F]{6}$/.test(this.value))document.getElementById('am-new-color').value=this.value">
        </div>
      </div>

      <div>
        ${_amLabel('시스템 프롬프트', AGENT_FIELD_HELP.system_prompt)}
        <textarea id="am-new-system-prompt" rows="4" style="${inputStyle};resize:vertical">${esc(preset?.system_prompt || '')}</textarea>
      </div>

      <div style="display:flex;gap:8px">
        <div style="flex:1">
          ${_amLabel('Temperature', AGENT_FIELD_HELP.temperature)}
          <input id="am-new-temperature" type="number" step="0.1" min="0" max="2" placeholder="기본값" style="${inputStyle}">
          <div class="field-hint">낮을수록 일관되고, 높을수록 다양한 답변이 나옵니다.</div>
        </div>
        <div style="flex:2">
          ${_amLabel('리뷰 포커스 (쉼표 구분)', AGENT_FIELD_HELP.review_focus)}
          <input id="am-new-review-focus" placeholder="security, performance, ..." style="${inputStyle}">
        </div>
      </div>

      <div class="am-action-footer">
        <div style="display:flex;align-items:center;gap:8px">
          <button class="btn" type="button" onclick="_amTestConnection('new', this)">연결 테스트</button>
          <span id="am-new-test-status" class="conn-test-status"></span>
        </div>
        <button class="btn btn-primary" onclick="_amSubmitNewAgent(this)" style="padding:8px 24px">추가</button>
      </div>
    </div>`;
  _amUpdateNewStrictnessUI();
  _amRefreshModelHint('new');
  _amRefreshConnectionHint('new');
  _amBindDirtyTracking();
}

export function _amUpdateNewStrictnessUI() {
  const checked = document.querySelector('input[name="am-new-strictness"]:checked');
  if (!checked) return;
  STRICTNESS_OPTIONS.forEach(so => {
    const label = document.getElementById('am-new-strictness-' + so.value)?.closest('label');
    if (!label) return;
    const sel = so.value === checked.value;
    label.style.borderColor = sel ? 'var(--accent)' : 'var(--border)';
    label.style.background = sel ? 'rgba(88,166,255,0.08)' : 'var(--bg)';
  });
}

export function _amOnNewClientTypeChange() {
  const ct = document.getElementById('am-new-client-type')?.value || 'claude-code';
  const providerRow = document.getElementById('am-new-provider-row');
  if (providerRow) providerRow.style.display = ct === 'opencode' ? '' : 'none';
  const iconEl = document.getElementById('am-new-client-type-icon');
  if (iconEl) iconEl.innerHTML = providerIconSvg(ct, 16);
  const modelSelect = document.getElementById('am-new-model-id-select');
  if (modelSelect) {
    const models = _amAvailableModels[ct] || [];
    modelSelect.innerHTML = '<option value="">기본값 (클라이언트 설정)</option>' + models.map(m => `<option value="${m.model_id}">${m.label}</option>`).join('');
  }
  _amRefreshModelHint('new');
  _amRefreshConnectionHint('new');
}

function _amRenderConnectionTestDetail(data) {
  const trigger = data?.trigger || {};
  const command = trigger.command || '';
  const output = trigger.output || '';
  const error = trigger.error || '';
  const prompt = data?.prompt || '';
  const hasResult = data?.type === 'result';

  let sections = '';

  // Status badge
  if (!hasResult) {
    const label = command ? '에이전트 완료, 콜백 대기 중...' : '에이전트 응답 대기 중...';
    sections += `<div class="conn-test-section"><span class="conn-test-label">Status</span><pre class="conn-test-code conn-test-pending">${esc(label)}</pre></div>`;
  } else if (data.ok) {
    sections += `<div class="conn-test-section"><span class="conn-test-label">Status</span><pre class="conn-test-code" style="color:var(--severity-dismissed)">성공 — 콜백 수신 완료</pre></div>`;
  } else {
    sections += `<div class="conn-test-section"><span class="conn-test-label">Status</span><pre class="conn-test-code conn-test-error">${esc(data.reason || data.status || '실패')}</pre></div>`;
  }

  if (command) {
    sections += `<div class="conn-test-section"><span class="conn-test-label">Command</span><pre class="conn-test-code">${esc(command)}</pre></div>`;
  }

  if (output) {
    sections += `<div class="conn-test-section"><span class="conn-test-label">Output</span><pre class="conn-test-code">${esc(output.length > 2000 ? output.slice(0, 2000) + '...' : output)}</pre></div>`;
  }

  if (error) {
    sections += `<div class="conn-test-section"><span class="conn-test-label">Error</span><pre class="conn-test-code conn-test-error">${esc(error.length > 2000 ? error.slice(0, 2000) + '...' : error)}</pre></div>`;
  }

  if (prompt) {
    sections += `<div class="conn-test-section"><span class="conn-test-label">Prompt</span><pre class="conn-test-code">${esc(prompt)}</pre></div>`;
  }

  const meta = {
    status: data?.status || '',
    elapsed_ms: data?.elapsed_ms ?? null,
    test_token: data?.test_token || '',
    session_marker: data?.session_marker || '',
    callback: data?.callback || null,
  };
  sections += `<div class="conn-test-section"><span class="conn-test-label">Meta</span><pre class="conn-test-json">${esc(JSON.stringify(meta, null, 2))}</pre></div>`;

  return `<div class="conn-test-detail">${sections}</div>`;
}

let _amConnTestDetailData = null;

function _amRefreshFloatBody() {
  const body = document.querySelector('#conn-test-float .conn-test-float-body');
  if (body && _amConnTestDetailData) {
    body.innerHTML = _amRenderConnectionTestDetail(_amConnTestDetailData);
  }
}

export function _amShowConnTestDetail() {
  if (!_amConnTestDetailData) return;
  const existing = document.getElementById('conn-test-float');
  if (existing) { existing.remove(); return; }

  const float = document.createElement('div');
  float.id = 'conn-test-float';
  float.className = 'conn-test-float';
  float.innerHTML = `<div class="conn-test-float-header"><span>연결 테스트 상세</span><button class="conn-test-float-close" onclick="document.getElementById('conn-test-float')?.remove()">&times;</button></div><div class="conn-test-float-body">${_amRenderConnectionTestDetail(_amConnTestDetailData)}</div>`;
  (_amOverlay || document.body).appendChild(float);
}

function _amSetTestStatus(statusEl, cls, text) {
  if (!statusEl) return;
  const detailBtn = ` <button class="btn conn-test-detail-btn" onclick="_amShowConnTestDetail()">상세</button>`;
  statusEl.className = 'conn-test-status ' + cls;
  statusEl.innerHTML = esc(text) + detailBtn;
}

export async function _amTestConnection(mode, btn) {
  const isNew = mode === 'new';
  const prefix = isNew ? 'am-new' : 'am';
  const statusEl = document.getElementById(`${prefix}-test-status`);
  const clientType = document.getElementById(`${prefix}-client-type`)?.value || 'claude-code';
  const provider = (document.getElementById(`${prefix}-provider`)?.value || '').trim();
  const modelId = (document.getElementById(`${prefix}-model-id`)?.value || '').trim();

  if (btn) btn.disabled = true;
  document.getElementById('conn-test-float')?.remove();
  _amConnTestDetailData = { status: 'pending' };

  const startTime = Date.now();
  let timerInterval = null;
  if (statusEl) {
    _amSetTestStatus(statusEl, '', '테스트 중... 0초');
    timerInterval = setInterval(() => {
      const sec = Math.floor((Date.now() - startTime) / 1000);
      _amSetTestStatus(statusEl, '', `테스트 중... ${sec}초`);
    }, 1000);
  }

  try {
    const r = await fetch('/api/agents/connection-test', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ client_type: clientType, provider, model_id: modelId, timeout_seconds: 60 }),
    });
    if (!r.ok) {
      const data = await r.json();
      _amConnTestDetailData = data;
      _amRefreshFloatBody();
      _amSetTestStatus(statusEl, 'fail', data.detail || '연결 테스트 실패');
      return;
    }

    const reader = r.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const lines = buf.split('\n');
      buf = lines.pop();
      for (const line of lines) {
        if (!line.trim()) continue;
        let ev;
        try { ev = JSON.parse(line); } catch { continue; }

        if (ev.type === 'started') {
          _amConnTestDetailData = { ...(_amConnTestDetailData || {}), ...ev, status: 'pending' };
          _amRefreshFloatBody();
        } else if (ev.type === 'trigger_done') {
          _amConnTestDetailData = { ...(_amConnTestDetailData || {}), ...ev };
          _amRefreshFloatBody();
        } else if (ev.type === 'result') {
          _amConnTestDetailData = { ...(_amConnTestDetailData || {}), ...ev };
          _amRefreshFloatBody();
          if (ev.ok) {
            const elapsed = ev.elapsed_ms != null ? `${(ev.elapsed_ms / 1000).toFixed(1)}초` : '-';
            _amSetTestStatus(statusEl, 'ok', `성공 (${elapsed})`);
          } else {
            const reason = ev.reason || ev.status || '연결 실패';
            _amSetTestStatus(statusEl, 'fail', `실패: ${reason}`);
          }
        }
      }
    }
  } catch (e) {
    _amConnTestDetailData = { reason: e.message, status: 'request_error' };
    _amRefreshFloatBody();
    _amSetTestStatus(statusEl, 'fail', `요청 실패: ${e.message}`);
  } finally {
    if (timerInterval) clearInterval(timerInterval);
    if (btn) btn.disabled = false;
  }
}

export async function _amSubmitNewAgent(btn) {
  btn.disabled = true;
  const tempVal = (document.getElementById('am-new-temperature')?.value || '').trim();
  const focusVal = (document.getElementById('am-new-review-focus')?.value || '').trim();
  const strictnessEl = document.querySelector('input[name="am-new-strictness"]:checked');
  const payload = {
    id: (document.getElementById('am-new-id')?.value || '').trim(),
    description: (document.getElementById('am-new-description')?.value || '').trim(),
    strictness: strictnessEl?.value || 'balanced',
    client_type: document.getElementById('am-new-client-type')?.value || 'claude-code',
    provider: (document.getElementById('am-new-provider')?.value || '').trim(),
    model_id: (document.getElementById('am-new-model-id')?.value || '').trim(),
    color: (document.getElementById('am-new-color-text')?.value || '').trim(),
    system_prompt: document.getElementById('am-new-system-prompt')?.value || '',
    temperature: tempVal ? parseFloat(tempVal) : null,
    review_focus: focusVal ? focusVal.split(',').map(s => s.trim()).filter(Boolean) : [],
  };
  if (!payload.id) { window.showToast('id는 필수입니다', 'error'); alert('id는 필수입니다'); btn.disabled = false; return; }
  try {
    const r = await fetch('/api/agent-presets', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload),
    });
    const data = await r.json();
    if (!r.ok) { window.showToast(data.detail || '프리셋 추가 실패', 'error'); alert(data.detail || '프리셋 추가 실패'); btn.disabled = false; return; }
    _amSetDirty(false);
    window.showToast('프리셋이 추가되었습니다.', 'success');
    window.dispatchEvent(new CustomEvent('agent-presets-updated'));
    // Re-fetch and show the new agent
    let agents = [];
    try { const r2 = await fetch('/api/agent-presets'); if (r2.ok) agents = await r2.json(); } catch (e) {}
    _amSelectedAgentId = payload.id;
    _amRenderList(agents);
    const mc = agents.find(a => a.id === payload.id);
    if (mc) _amRenderEditForm(mc, agents);
  } catch (e) {
    window.showToast('프리셋 추가 실패', 'error');
    alert('프리셋 추가 실패');
    btn.disabled = false;
  }
}

// --- Redirect legacy functions ---
export async function openAddAgentModal() { await openAgentManager(); }
export async function openEditAgentModal(modelId) { await openAgentManager(modelId); }

export async function removeAgent(modelId) {
  if (!confirm(`${modelId} 에이전트를 제거하시겠습니까?`)) return;
  try {
    const r = await fetch(`/api/sessions/current/agents/${modelId}`, { method: 'DELETE' });
    const data = await r.json();
    if (!r.ok) { alert(data.detail || '에이전트 제거 실패'); return; }
    await window.pollStatus();
  } catch (e) {
    alert('에이전트 제거 실패');
  }
}

export async function toggleAgentEnabled(modelId) {
  const agent = state.agents.find(a => a.model_id === modelId);
  if (!agent) return;
  const newEnabled = agent.enabled === false ? true : false;
  try {
    const r = await fetch(`/api/sessions/current/agents/${modelId}`, {
      method: 'PUT',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ enabled: newEnabled }),
    });
    if (!r.ok) { const e = await r.json(); alert(e.detail || '변경 실패'); return; }
    await window.pollStatus();
  } catch (e) { alert('변경 실패'); }
}

export function getUniqueAgentId(baseId) {
  const existing = _amPresetCache.map(a => a.id);
  if (!existing.includes(baseId)) return baseId;
  for (let i = 2; ; i++) {
    const candidate = `${baseId}-${i}`;
    if (!existing.includes(candidate)) return candidate;
  }
}
