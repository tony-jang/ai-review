// Agent Manager Modal

import { REVIEW_PRESETS, STRICTNESS_OPTIONS, AGENT_FIELD_HELP, MODEL_DEFAULT_HINTS, AGENT_PRESETS } from '../constants.js';
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

  _amRenderList(presets);

  if (editModelId) {
    _amSelectedAgentId = editModelId;
    _amMode = 'edit';
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
        ${_amLabel('빠른 설정', '포커스/프롬프트를 빠르게 채웁니다.')}
        <div style="display:flex;gap:6px;flex-wrap:wrap">
          ${REVIEW_PRESETS.map(rp => `<button class="btn" onclick="_amApplyPreset('${rp.key}')" style="font-size:11px;padding:4px 10px">${rp.icon} ${rp.label}</button>`).join('')}
        </div>
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
          <select id="am-client-type" style="${inputStyle}" onchange="_amOnClientTypeChange()">
            ${['claude-code','codex','opencode','gemini'].map(t => `<option value="${t}" ${clientType === t ? 'selected' : ''}>${t}</option>`).join('')}
          </select>
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

      <div>
        ${_amLabel('연결 테스트 Endpoint (자동)', AGENT_FIELD_HELP.test_endpoint)}
        <div id="am-test-target" class="field-hint"></div>
        <div style="margin-top:6px">
          <button class="btn" type="button" onclick="_amTestConnection('edit', this)">연결 테스트</button>
        </div>
        <div id="am-test-result" class="conn-test-result"></div>
      </div>

      <div class="am-action-footer">
        <label style="display:flex;align-items:center;gap:8px;font-size:13px;cursor:pointer">
          <input type="checkbox" id="am-enabled" ${mc.enabled !== false ? 'checked' : ''} style="accent-color:var(--accent)">
          활성화
        </label>
        <button class="btn btn-primary" onclick="_amSaveAgent('${esc(mc.id)}')" style="padding:8px 24px">저장</button>
      </div>
    </div>`;
  _amUpdateStrictnessUI();
  _amRefreshModelHint('edit');
  _amRefreshConnectionHint('edit');
  _amBindDirtyTracking();
}

export function _amApplyPreset(key) {
  const rp = REVIEW_PRESETS.find(r => r.key === key);
  if (!rp) return;
  const focusEl = document.getElementById('am-review-focus');
  const promptEl = document.getElementById('am-system-prompt');
  if (focusEl) focusEl.value = rp.review_focus.join(', ');
  if (promptEl) promptEl.value = rp.system_prompt;
  _amSetDirty(true);
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

export function _amShowAddPanel(force = false) {
  if (!force && !_amConfirmDiscardChanges('프리셋 추가 화면으로 이동')) return;
  _amSelectedAgentId = null;
  _amMode = 'add';
  // Update left list selection
  try {
    const listEl = _amOverlay?.querySelector('#am-agent-list');
    // Just de-select visually
    if (listEl) {
      listEl.querySelectorAll('[onclick^="_amSelectAgent"]').forEach(el => {
        el.style.background = 'transparent';
        el.style.borderLeft = '3px solid transparent';
      });
    }
  } catch (e) {}

  const right = _amOverlay?.querySelector('#am-right');
  if (!right) return;
  const inputStyle = 'background:var(--bg);color:var(--text);border:1px solid var(--border);border-radius:6px;padding:8px 12px;font-size:13px;font-family:inherit;width:100%';

  right.innerHTML = `
    <div style="display:flex;align-items:center;gap:8px;margin-bottom:16px">
      <h3 style="font-size:14px;font-weight:600">프리셋 추가</h3>
      <span id="am-dirty-indicator" class="am-dirty-indicator" style="display:none">저장 안 됨</span>
    </div>
    <div style="margin-bottom:16px">
      <label style="font-size:11px;color:var(--text-muted);margin-bottom:6px;display:block">클라이언트 프리셋</label>
      <div class="preset-grid" style="grid-template-columns:repeat(3,1fr);gap:8px">
        ${AGENT_PRESETS.map((p, i) => `<div class="preset-card" onclick="_amSelectAddPreset(${i})" style="padding:12px 8px"><span class="preset-icon" style="font-size:20px">${p.icon}</span><span class="preset-label" style="font-size:12px">${p.label}</span></div>`).join('')}
        <div class="preset-card preset-card-custom" onclick="_amSelectAddPreset(-1)" style="padding:12px 8px"><span class="preset-icon" style="font-size:20px">⚙️</span><span class="preset-label" style="font-size:12px">커스텀</span></div>
      </div>
    </div>
    <div id="am-add-form" style="display:none"></div>`;
  _amSetDirty(false);
}

export function _amSelectAddPreset(presetIdx) {
  const isCustom = presetIdx === -1;
  const preset = isCustom ? null : AGENT_PRESETS[presetIdx];
  const inputStyle = 'background:var(--bg);color:var(--text);border:1px solid var(--border);border-radius:6px;padding:8px 12px;font-size:13px;font-family:inherit;width:100%';
  const defaultId = preset ? window.getUniqueAgentId(preset.id) : '';
  const defaultColor = preset ? preset.color : '#8B5CF6';
  const defaultClientType = preset ? preset.client_type : 'claude-code';
  const showProvider = isCustom || (preset && preset.needsProvider);
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
        ${_amLabel('빠른 설정', '포커스/프롬프트를 빠르게 채웁니다.')}
        <div style="display:flex;gap:6px;flex-wrap:wrap">
          ${REVIEW_PRESETS.map(rp => `<button class="btn" onclick="_amApplyNewPreset('${rp.key}')" style="font-size:11px;padding:4px 10px">${rp.icon} ${rp.label}</button>`).join('')}
        </div>
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
          <select id="am-new-client-type" style="${inputStyle}" ${!isCustom ? 'disabled' : ''} onchange="_amOnNewClientTypeChange()">
            ${['claude-code','codex','opencode','gemini'].map(t => `<option value="${t}" ${defaultClientType === t ? 'selected' : ''}>${t}</option>`).join('')}
          </select>
        </div>
        <div style="flex:1">
          ${_amLabel('세부 모델', AGENT_FIELD_HELP.model_id)}
          <select id="am-new-model-id-select" style="${inputStyle}" onchange="document.getElementById('am-new-model-id').value=this.value">
            <option value="">기본값 (클라이언트 설정)</option>
            ${models.map(m => `<option value="${m.model_id}">${m.label}</option>`).join('')}
          </select>
          <input id="am-new-model-id" value="" placeholder="또는 직접 입력" style="${inputStyle};margin-top:4px;font-size:11px">
          <div id="am-new-model-default-hint" class="field-hint"></div>
          <div class="field-hint">프리셋은 일부만 노출됩니다. 필요한 모델은 직접 입력하세요.</div>
        </div>
      </div>

      <div id="am-new-provider-row" style="${showProvider ? '' : 'display:none'}">
        ${_amLabel('Provider', AGENT_FIELD_HELP.provider)}
        <input id="am-new-provider" placeholder="provider (예: openai)" style="${inputStyle}">
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
        <textarea id="am-new-system-prompt" rows="2" placeholder="시스템 프롬프트 (선택)" style="${inputStyle};resize:vertical"></textarea>
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

      <div>
        ${_amLabel('연결 테스트 Endpoint (자동)', AGENT_FIELD_HELP.test_endpoint)}
        <div id="am-new-test-target" class="field-hint"></div>
        <div style="margin-top:6px">
          <button class="btn" type="button" onclick="_amTestConnection('new', this)">연결 테스트</button>
        </div>
        <div id="am-new-test-result" class="conn-test-result"></div>
      </div>

      <button class="btn btn-primary" onclick="_amSubmitNewAgent(this)" style="align-self:flex-end;padding:8px 24px">추가</button>
    </div>`;
  _amUpdateNewStrictnessUI();
  _amRefreshModelHint('new');
  _amRefreshConnectionHint('new');
  _amBindDirtyTracking();
}

export function _amApplyNewPreset(key) {
  const rp = REVIEW_PRESETS.find(r => r.key === key);
  if (!rp) return;
  const focusEl = document.getElementById('am-new-review-focus');
  const promptEl = document.getElementById('am-new-system-prompt');
  if (focusEl) focusEl.value = rp.review_focus.join(', ');
  if (promptEl) promptEl.value = rp.system_prompt;
  _amSetDirty(true);
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
  const modelSelect = document.getElementById('am-new-model-id-select');
  if (modelSelect) {
    const models = _amAvailableModels[ct] || [];
    modelSelect.innerHTML = '<option value="">기본값 (클라이언트 설정)</option>' + models.map(m => `<option value="${m.model_id}">${m.label}</option>`).join('');
  }
  _amRefreshModelHint('new');
  _amRefreshConnectionHint('new');
}

function _amRenderConnectionTestDetail(data) {
  const detail = {
    status: data?.status || '',
    elapsed_ms: data?.elapsed_ms ?? null,
    reason: data?.reason || data?.detail || '',
    test_token: data?.test_token || '',
    session_marker: data?.session_marker || '',
    callback: data?.callback || null,
    trigger: data?.trigger || null,
  };
  return `<details class="conn-test-detail"><summary>상세 보기</summary><pre class="conn-test-json">${esc(JSON.stringify(detail, null, 2))}</pre></details>`;
}

export async function _amTestConnection(mode, btn) {
  const isNew = mode === 'new';
  const prefix = isNew ? 'am-new' : 'am';
  const resultEl = document.getElementById(`${prefix}-test-result`);
  const clientType = document.getElementById(`${prefix}-client-type`)?.value || 'claude-code';
  const provider = (document.getElementById(`${prefix}-provider`)?.value || '').trim();
  const modelId = (document.getElementById(`${prefix}-model-id`)?.value || '').trim();

  if (btn) btn.disabled = true;
  const startTime = Date.now();
  let timerInterval = null;
  if (resultEl) {
    resultEl.className = 'conn-test-result';
    resultEl.textContent = '테스트 중... 0초';
    timerInterval = setInterval(() => {
      const sec = Math.floor((Date.now() - startTime) / 1000);
      resultEl.textContent = `테스트 중... ${sec}초`;
    }, 1000);
  }

  try {
    const r = await fetch('/api/agents/connection-test', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ client_type: clientType, provider, model_id: modelId, timeout_seconds: 60 }),
    });
    const data = await r.json();
    if (!r.ok) {
      if (resultEl) {
        resultEl.className = 'conn-test-result fail';
        resultEl.innerHTML = `${esc(data.detail || '연결 테스트 실패')}${_amRenderConnectionTestDetail(data)}`;
      }
      return;
    }
    if (data.ok) {
      if (resultEl) {
        const elapsed = data.elapsed_ms != null ? `${(data.elapsed_ms / 1000).toFixed(1)}초` : '-';
        resultEl.className = 'conn-test-result ok';
        resultEl.innerHTML = `${esc(`성공 (${elapsed}) · 콜백 수신 완료`)}${_amRenderConnectionTestDetail(data)}`;
      }
    } else if (resultEl) {
      resultEl.className = 'conn-test-result fail';
      const reason = data.reason || data.error || data.status || '연결 실패';
      resultEl.innerHTML = `${esc(`실패: ${reason}`)}${_amRenderConnectionTestDetail(data)}`;
    }
  } catch (e) {
    if (resultEl) {
      resultEl.className = 'conn-test-result fail';
      resultEl.innerHTML = `${esc(`요청 실패: ${e.message}`)}${_amRenderConnectionTestDetail({ reason: e.message, status: 'request_error' })}`;
    }
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
