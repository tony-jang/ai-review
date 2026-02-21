// New Session Modal

import { esc } from '../utils.js';
import state from '../state.js';

// Module-local state
let _nsmBranches = [];
let _nsmBaseValue = '';
let _nsmHeadValue = '';
let _nsmPresetList = [];
let _nsmSelectedPresetIds = [];
let _nsmRepoValidated = false;

function _nsmCanSubmit() {
  return _nsmRepoValidated && _nsmSelectedPresetIds.length > 0;
}

function _nsmUpdateSubmitState() {
  const submitBtn = document.getElementById('nsm-submit');
  if (submitBtn) submitBtn.disabled = !_nsmCanSubmit();
}

export async function _nsmRefreshPresets(preserveSelection = true) {
  const prev = [..._nsmSelectedPresetIds];
  let list = [];
  try {
    const r = await fetch('/api/agent-presets');
    if (r.ok) list = await r.json();
  } catch (e) {}

  _nsmPresetList = list;
  const ids = _nsmPresetList.map(p => p.id);
  const idSet = new Set(ids);

  if (!preserveSelection) {
    _nsmSelectedPresetIds = [...ids];
  } else {
    let selected = prev.filter(id => idSet.has(id));
    if (!prev.length && ids.length) selected = [...ids];
    if (prev.length && !selected.length && ids.length) selected = [...ids];
    _nsmSelectedPresetIds = selected;
  }

  _nsmRenderPresetList();
  _nsmUpdateSubmitState();
}

export async function _nsmOpenPresetManager(editModelId = null) {
  if (editModelId) {
    await window.openEditAgentModal(editModelId);
    return;
  }
  await window.openAgentManager();
}

export async function _nsmOpenPresetAdd() {
  await window.openAgentManager();
  window._amShowAddPanel(true);
}

export function _nsmTogglePreset(id) {
  if (_nsmSelectedPresetIds.includes(id)) {
    _nsmSelectedPresetIds = _nsmSelectedPresetIds.filter(v => v !== id);
  } else {
    _nsmSelectedPresetIds.push(id);
  }
  _nsmRenderPresetList();
  _nsmUpdateSubmitState();
}

function _nsmRenderPresetList() {
  const wrap = document.getElementById('nsm-preset-list');
  if (!wrap) return;
  if (!_nsmPresetList.length) {
    wrap.innerHTML = `<div style="grid-column:1 / -1;padding:10px;border:1px dashed var(--border);border-radius:8px;color:var(--text-muted);font-size:12px;display:flex;align-items:center;justify-content:space-between;gap:8px">
      <span>등록된 프리셋이 없습니다.</span>
      <button class="btn" type="button" onclick="_nsmOpenPresetAdd()">프리셋 추가</button>
    </div>`;
    return;
  }
  wrap.innerHTML = _nsmPresetList.map((p) => {
    const checked = _nsmSelectedPresetIds.includes(p.id);
    const color = p.color || '#8B949E';
    const encoded = encodeURIComponent(p.id);
    return `<label style="display:flex;align-items:center;gap:8px;padding:8px 10px;border:1px solid ${checked ? 'var(--accent)' : 'var(--border)'};border-radius:8px;cursor:pointer;background:${checked ? 'rgba(88,166,255,0.08)' : 'var(--bg)'}">
      <input type="checkbox" ${checked ? 'checked' : ''} onchange="_nsmTogglePreset(decodeURIComponent('${encoded}'))" style="accent-color:var(--accent)">
      <span style="width:10px;height:10px;border-radius:999px;background:${color};display:inline-block"></span>
      <span style="font-size:12px;font-weight:600">${esc(p.id)}</span>
      <span style="font-size:11px;color:var(--text-muted)">${esc(p.description || p.client_type || '')}</span>
      <button class="btn" type="button" style="margin-left:auto;font-size:10px;padding:2px 6px" onclick="event.preventDefault();event.stopPropagation();_nsmOpenPresetManager(decodeURIComponent('${encoded}'))">수정</button>
    </label>`;
  }).join('');
}

export function _openBranchPicker(target, currentValue, onSelect) {
  const existing = document.querySelector('.branch-picker-overlay');
  if (existing) existing.remove();

  const locals = _nsmBranches.filter(b => b.type === 'local');
  const remotes = _nsmBranches.filter(b => b.type === 'remote');

  const overlay = document.createElement('div');
  overlay.className = 'branch-picker-overlay';

  function renderList(filter) {
    const q = (filter || '').toLowerCase();
    const fl = locals.filter(b => b.name.toLowerCase().includes(q));
    const fr = remotes.filter(b => b.name.toLowerCase().includes(q));
    if (!fl.length && !fr.length) return '<div class="bp-empty">일치하는 브랜치 없음</div>';
    let html = '';
    if (fl.length) {
      html += '<div class="bp-group-label">Local</div>';
      fl.forEach(b => {
        const sel = b.name === currentValue ? ' selected' : '';
        const check = b.name === currentValue ? '\u2713' : '';
        html += `<div class="bp-item${sel}" data-value="${esc(b.name)}"><span class="bp-check">${check}</span>${esc(b.name)}</div>`;
      });
    }
    if (fr.length) {
      html += '<div class="bp-group-label">Remote</div>';
      fr.forEach(b => {
        const sel = b.name === currentValue ? ' selected' : '';
        const check = b.name === currentValue ? '\u2713' : '';
        html += `<div class="bp-item${sel}" data-value="${esc(b.name)}"><span class="bp-check">${check}</span>${esc(b.name)}</div>`;
      });
    }
    return html;
  }

  const card = document.createElement('div');
  card.className = 'branch-picker-card';
  card.innerHTML = `<input class="bp-search" placeholder="브랜치 검색..." autofocus><div class="bp-list">${renderList('')}</div>`;
  overlay.appendChild(card);
  document.body.appendChild(overlay);

  const searchInput = card.querySelector('.bp-search');
  const listEl = card.querySelector('.bp-list');

  searchInput.addEventListener('input', () => { listEl.innerHTML = renderList(searchInput.value); });
  listEl.addEventListener('click', (e) => {
    const item = e.target.closest('.bp-item');
    if (!item) return;
    const val = item.dataset.value;
    onSelect(val);
    overlay.remove();
  });
  overlay.addEventListener('click', (e) => { if (e.target === overlay) overlay.remove(); });
  searchInput.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') overlay.remove();
    if (e.key === 'Enter') {
      const first = listEl.querySelector('.bp-item');
      if (first) { onSelect(first.dataset.value); overlay.remove(); }
    }
  });
}

export function _getRecentRepos() {
  try { return JSON.parse(localStorage.getItem('ai-review-recent-repos') || '[]'); } catch { return []; }
}

export function _saveRecentRepo(path) {
  if (!path) return;
  let repos = _getRecentRepos().filter(r => r !== path);
  repos.unshift(path);
  if (repos.length > 5) repos = repos.slice(0, 5);
  localStorage.setItem('ai-review-recent-repos', JSON.stringify(repos));
}

export async function openNewSessionModal() {
  _nsmBranches = [];
  _nsmBaseValue = '';
  _nsmHeadValue = '';
  _nsmRepoValidated = false;
  _nsmPresetList = [];
  _nsmSelectedPresetIds = [];

  const overlay = document.createElement('div');
  overlay.className = 'report-overlay';
  const inputStyle = 'background:var(--bg);color:var(--text);border:1px solid var(--border);border-radius:6px;padding:8px 12px;font-size:13px;font-family:inherit;width:100%';
  const recentRepos = _getRecentRepos();
  const recentHtml = recentRepos.length
    ? `<div id="nsm-recent" style="display:flex;flex-wrap:wrap;gap:6px">${recentRepos.map(r => {
        const short = r.split('/').pop();
        return `<button type="button" class="btn" style="font-size:11px;padding:3px 8px" onclick="document.getElementById('nsm-repo-path').value='${esc(r)}';_nsmValidateRepo()">${esc(short)}</button>`;
      }).join('')}</div>`
    : '';

  overlay.innerHTML = `<div class="report-card" style="max-width:480px">
    <div class="report-header"><h2>새 리뷰 세션</h2><button class="report-close" onclick="this.closest('.report-overlay').remove()">\u2715</button></div>
    <div style="padding:20px;display:flex;flex-direction:column;gap:14px">
      <div>
        <label style="font-size:12px;color:var(--text-muted);display:block;margin-bottom:4px">Git 레포 경로</label>
        <div style="display:flex;gap:8px">
          <input id="nsm-repo-path" placeholder="/Users/.../project" style="${inputStyle};flex:1">
          <button class="btn" type="button" onclick="_nsmPickRepoPath()" title="폴더 선택" style="width:38px;padding:0">...</button>
          <button class="btn" id="nsm-validate-btn" onclick="_nsmValidateRepo()" style="white-space:nowrap">검증</button>
        </div>
        <div id="nsm-repo-status" style="font-size:12px;margin-top:6px"></div>
      </div>
      <div>
        <div style="display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:6px">
          <label style="font-size:12px;color:var(--text-muted);display:block">에이전트 프리셋</label>
          <div style="display:flex;align-items:center;gap:6px">
            <button class="btn" type="button" style="font-size:11px;padding:3px 8px" onclick="_nsmOpenPresetAdd()">추가</button>
            <button class="btn" type="button" style="font-size:11px;padding:3px 8px" onclick="_nsmOpenPresetManager()">관리</button>
            <button class="btn" type="button" style="font-size:11px;padding:3px 8px" onclick="_nsmRefreshPresets(true)">새로고침</button>
          </div>
        </div>
        <div id="nsm-preset-list" style="display:grid;grid-template-columns:1fr 1fr;gap:8px"></div>
        <div style="font-size:11px;color:var(--text-muted);margin-top:6px">선택한 프리셋의 리뷰어로 세션이 시작됩니다.</div>
      </div>
      ${recentRepos.length ? `<div><label style="font-size:12px;color:var(--text-muted);display:block;margin-bottom:4px">최근 사용</label>${recentHtml}</div>` : ''}
      <div id="nsm-branch-section" style="display:none">
        <div style="display:flex;gap:12px">
          <div style="flex:1">
            <label style="font-size:12px;color:var(--text-muted);display:block;margin-bottom:4px">머지 대상 (Base)</label>
            <button type="button" class="branch-picker-trigger" id="nsm-base-btn" onclick="_openBranchPicker(this, _nsmBaseValue, v=>{_nsmBaseValue=v;this.querySelector('.bp-label').textContent=v;})">
              <span class="bp-label">선택...</span><span class="bp-arrow">\u25BC</span>
            </button>
          </div>
          <div style="flex:1">
            <label style="font-size:12px;color:var(--text-muted);display:block;margin-bottom:4px">작업 브랜치 (Head)</label>
            <button type="button" class="branch-picker-trigger" id="nsm-head-btn" onclick="_openBranchPicker(this, _nsmHeadValue, v=>{_nsmHeadValue=v;this.querySelector('.bp-label').textContent=v;})">
              <span class="bp-label">선택...</span><span class="bp-arrow">\u25BC</span>
            </button>
          </div>
        </div>
      </div>
      <details id="nsm-context-section" style="margin-top:12px">
        <summary style="cursor:pointer;font-weight:600;font-size:13px;color:var(--text-secondary)">변경 설명 (선택)</summary>
        <div style="margin-top:8px">
          <textarea id="nsm-context-summary" placeholder="이 변경의 목적과 핵심 내용..."
                    rows="3" style="width:100%;resize:vertical;font-size:13px;padding:8px;border:1px solid var(--border);border-radius:6px;background:var(--bg-secondary);color:var(--text-primary);box-sizing:border-box"></textarea>
          <textarea id="nsm-context-decisions" placeholder="의도적 결정 (줄별 하나씩)"
                    rows="2" style="width:100%;resize:vertical;font-size:13px;padding:8px;margin-top:6px;border:1px solid var(--border);border-radius:6px;background:var(--bg-secondary);color:var(--text-primary);box-sizing:border-box"></textarea>
        </div>
      </details>
      <button class="btn btn-primary" id="nsm-submit" onclick="submitNewSession(this)" style="align-self:flex-end" disabled>생성</button>
    </div>
  </div>`;
  document.body.appendChild(overlay);
  overlay.addEventListener('click', (e) => { if (e.target === overlay) overlay.remove(); });

  const repoInput = overlay.querySelector('#nsm-repo-path');
  repoInput.focus();
  let _debounce;
  repoInput.addEventListener('input', () => { clearTimeout(_debounce); _debounce = setTimeout(() => window._nsmValidateRepo(), 500); });
  repoInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); window._nsmValidateRepo(); } });
  await _nsmRefreshPresets(false);
}

export async function _nsmPickRepoPath() {
  const statusEl = document.getElementById('nsm-repo-status');
  const pathInput = document.getElementById('nsm-repo-path');
  if (!statusEl || !pathInput) return;

  statusEl.innerHTML = '<span style="color:var(--text-muted)">폴더 선택기 여는 중...</span>';
  try {
    const pickResult = await _nsmRequestDirectoryPath();
    if (!pickResult.ok) {
      const msg = pickResult.detail || '폴더 선택 실패';
      statusEl.innerHTML = `<span style="color:var(--severity-critical)">\u274C ${esc(msg)}</span>`;
      return;
    }
    const data = pickResult.data || {};
    if (data.cancelled || !data.path) {
      statusEl.innerHTML = '<span style="color:var(--text-muted)">폴더 선택이 취소되었습니다.</span>';
      return;
    }
    pathInput.value = data.path;
    await window._nsmValidateRepo();
  } catch (e) {
    statusEl.innerHTML = `<span style="color:var(--severity-critical)">\u274C 폴더 선택 실패: ${esc(e.message)}</span>`;
  }
}

async function _nsmRequestDirectoryPath() {
  const endpoints = ['/api/fs/pick-directory', '/api/pick-directory'];
  let lastDetail = '';

  for (const endpoint of endpoints) {
    try {
      const r = await fetch(endpoint);
      let data = {};
      try {
        data = await r.json();
      } catch (e) {}
      if (r.ok) return { ok: true, data };
      const detail = (data && data.detail) ? String(data.detail) : `${r.status} ${r.statusText}`.trim();
      if (r.status === 404) {
        lastDetail = detail || 'Not found';
        continue;
      }
      return { ok: false, detail: detail || '폴더 선택 실패' };
    } catch (e) {
      lastDetail = e?.message || String(e);
    }
  }

  return {
    ok: false,
    detail: `폴더 선택 API를 찾지 못했습니다. (${lastDetail || 'Not found'})`,
  };
}

export async function _nsmValidateRepo() {
  const pathInput = document.getElementById('nsm-repo-path');
  const statusEl = document.getElementById('nsm-repo-status');
  const branchSection = document.getElementById('nsm-branch-section');
  const path = pathInput.value.trim();
  if (!path) {
    statusEl.textContent = '';
    branchSection.style.display = 'none';
    _nsmRepoValidated = false;
    _nsmUpdateSubmitState();
    return;
  }
  statusEl.innerHTML = '<span style="color:var(--text-muted)">검증 중...</span>';
  try {
    const r = await fetch('/api/git/validate', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({path}) });
    const data = await r.json();
    if (!data.valid) {
      statusEl.innerHTML = `<span style="color:var(--severity-critical)">\u274C ${esc(data.error || '유효하지 않은 경로')}</span>`;
      branchSection.style.display = 'none';
      _nsmRepoValidated = false;
      _nsmUpdateSubmitState();
      return;
    }
    pathInput.value = data.root;
    statusEl.innerHTML = `<span style="color:var(--severity-dismissed)">\u2705 유효한 Git 저장소 (${esc(data.current_branch)})</span>`;
    await _nsmLoadBranches(data.root, data.current_branch);
  } catch (e) {
    statusEl.innerHTML = `<span style="color:var(--severity-critical)">\u274C 검증 실패</span>`;
    branchSection.style.display = 'none';
    _nsmRepoValidated = false;
    _nsmUpdateSubmitState();
  }
}

async function _nsmLoadBranches(repoPath, currentBranch) {
  const branchSection = document.getElementById('nsm-branch-section');
  try {
    const r = await fetch(`/api/git/branches?repo_path=${encodeURIComponent(repoPath)}`);
    _nsmBranches = await r.json();

    const locals = _nsmBranches.filter(b => b.type === 'local');
    const remotes = _nsmBranches.filter(b => b.type === 'remote');

    // base default: origin/main > origin/master > main > first
    _nsmBaseValue = remotes.find(b => b.name === 'origin/main')?.name
      || remotes.find(b => b.name === 'origin/master')?.name
      || locals.find(b => b.name === 'main')?.name
      || locals.find(b => b.name === 'master')?.name
      || (_nsmBranches[0]?.name || 'main');
    _nsmHeadValue = currentBranch;

    document.querySelector('#nsm-base-btn .bp-label').textContent = _nsmBaseValue;
    document.querySelector('#nsm-head-btn .bp-label').textContent = _nsmHeadValue;

    branchSection.style.display = '';
    _nsmRepoValidated = true;
    _nsmUpdateSubmitState();
  } catch (e) {
    branchSection.style.display = 'none';
    _nsmRepoValidated = false;
    _nsmUpdateSubmitState();
  }
}

export async function submitNewSession(btn) {
  btn.disabled = true;
  const repoPath = document.getElementById('nsm-repo-path').value.trim();
  const base = _nsmBaseValue;
  const head = _nsmHeadValue;
  const presetIds = [..._nsmSelectedPresetIds];
  if (!presetIds.length) {
    alert('최소 1개 프리셋을 선택해주세요.');
    btn.disabled = false;
    return;
  }

  const contextSummary = (document.getElementById('nsm-context-summary')?.value || '').trim();
  const contextDecisions = (document.getElementById('nsm-context-decisions')?.value || '').trim();

  const body = { repo_path: repoPath, base, head, preset_ids: presetIds };

  if (contextSummary || contextDecisions) {
    body.implementation_context = {
      summary: contextSummary || '',
      decisions: contextDecisions ? contextDecisions.split('\n').filter(Boolean) : [],
    };
  }

  try {
    // 1) Create session
    const r = await fetch('/api/sessions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const data = await r.json();
    if (!r.ok) { alert(data.detail || '세션 생성 실패'); btn.disabled = false; return; }

    // 2) Start review
    const startR = await fetch(`/api/sessions/${data.session_id}/start`, { method: 'POST' });
    if (!startR.ok) { alert('리뷰 시작 실패'); }

    _saveRecentRepo(repoPath);
    btn.closest('.report-overlay').remove();
    await window.fetchSessions();
    await window.switchSession(data.session_id);
  } catch (e) {
    alert('세션 생성 실패');
    btn.disabled = false;
  }
}
