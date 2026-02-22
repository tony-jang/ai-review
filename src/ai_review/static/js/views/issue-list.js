import state, { uiState, _uiSaveStateToStorage } from '../state.js';
import { esc, _escapeAttr, getModelColor, _issueRangeLabel, progressBadgeHtml } from '../utils.js';
import { SEVERITY_COLORS, SEVERITY_ICONS, SEVERITY_LABELS } from '../constants.js';

function sevLabel(s) { return SEVERITY_LABELS[s]||s; }

function normalizeTitle(title) {
  return (title || '')
    .toLowerCase()
    .replace(/[^a-z0-9가-힣\s]/g, ' ')
    .split(/\s+/)
    .filter(w => w.length > 1)
    .sort()
    .slice(0, 4)
    .join(' ');
}

export function issueGroupKey(issue) {
  return `${issue.file}::${normalizeTitle(issue.title) || 'misc'}`;
}

export function getFilteredIssues() {
  const severity = document.getElementById('filter-severity')?.value || '';
  const agent = document.getElementById('filter-agent')?.value || '';
  const consensus = document.getElementById('filter-consensus')?.value || '';
  const progress = document.getElementById('filter-progress')?.value || '';
  const mineOnly = !!document.getElementById('filter-mine')?.checked;
  const coreOnly = !!document.getElementById('filter-core-only')?.checked;

  return state.issues.filter((issue) => {
    const sev = issue.final_severity || issue.severity;
    if (severity && sev !== severity) return false;
    if (progress && (issue.progress_status || 'reported') !== progress) return false;
    if (agent) {
      const raised = issue.raised_by === agent;
      const inThread = (issue.thread || []).some(op => op.model_id === agent);
      if (!raised && !inThread) return false;
    }
    if (consensus === 'yes' && issue.consensus !== true) return false;
    if (consensus === 'no' && issue.consensus === true) return false;
    if (mineOnly) {
      const mine = (issue.thread || []).some(op => op.model_id === 'human');
      if (!mine) return false;
    }
    if (coreOnly) {
      const severe = sev === 'critical' || sev === 'high';
      if (!severe && issue.consensus === true) return false;
    }
    return true;
  });
}

export function ensureIssueDisplayNumbers() {
  for (const issue of state.issues) {
    if (!state.issueNumberById[issue.id]) {
      state.issueNumberById[issue.id] = state.nextIssueNumber++;
    }
  }
}

export function renderIssueList() {
  const el = document.getElementById('issue-list');
  window._hideFileHoverTooltip();
  ensureIssueDisplayNumbers();
  const filtered = getFilteredIssues();
  if (!filtered.length) {
    el.innerHTML = '<div class="empty-state"><div class="icon">&#128269;</div><div class="message">조건에 맞는 이슈가 없습니다</div><div class="hint">필터를 조정해보세요</div></div>';
    _uiSaveStateToStorage();
    return;
  }

  const grouped = {};
  for (const issue of filtered) {
    const key = issueGroupKey(issue);
    grouped[key] = grouped[key] || [];
    grouped[key].push(issue);
  }

  el.innerHTML = Object.keys(grouped).sort().map((gk) => {
    const items = grouped[gk];
    const collapsed = !!state.collapsedIssueGroups[gk];
    const toggleCls = collapsed ? 'issue-group-toggle collapsed' : 'issue-group-toggle';
    const title = `${items[0].file} · ${items.length}개`;
    const encodedKey = encodeURIComponent(gk);
    const groupHeader = `<div class="issue-group-header" data-full-path="${_escapeAttr(title)}" onclick="toggleIssueGroup(decodeURIComponent('${encodedKey}'))" onmouseenter="onFileTreeRowEnter(event,this)" onmousemove="onFileTreeRowMove(event)" onmouseleave="onFileTreeRowLeave()"><span class="${toggleCls}">▾</span><span class="issue-group-label">${esc(title)}</span></div>`;
    if (collapsed) return groupHeader;
    return groupHeader + items.map((issue) => {
    const displayNo = state.issueNumberById[issue.id] || 0;
    const sev = issue.final_severity || issue.severity;
    const icon = SEVERITY_ICONS[sev]||'\u26AA';
    const color = SEVERITY_COLORS[sev]||'#6B7280';
    const active = state.selectedIssue===issue.id?' active':'';
    const cLabel = issue.consensus===true?'\u2705':issue.consensus===false?'\u26A0':'\u23F3';
    const raisedBy = issue.raised_by||'';
    const rangeLabel = _issueRangeLabel(issue);
    const itemHover = `#${displayNo} ${issue.title || ''}`;
    return `<div class="issue-item${active}" data-issue-id="${_escapeAttr(issue.id)}" data-full-path="${_escapeAttr(itemHover)}" onclick="selectIssue('${issue.id}')" onmouseenter="onFileTreeRowEnter(event,this)" onmousemove="onFileTreeRowMove(event)" onmouseleave="onFileTreeRowLeave()">
      <span class="issue-icon">${icon}</span>
      <div class="issue-info">
        <div class="issue-title">#${displayNo} ${esc(issue.title)}</div>
        <div class="issue-meta">
          <span class="severity-badge" style="background:${color}20;color:${color}">${sevLabel(sev)}</span>
          ${progressBadgeHtml(issue.progress_status)}
          <span>${cLabel}</span>
          ${rangeLabel
            ? `<span style="font-family:'SF Mono',Monaco,monospace;color:var(--text-muted)">L${esc(rangeLabel)}</span>`
            : `<span style="color:var(--severity-high);font-size:11px">라인 미지정</span>`}
          <span style="color:${getModelColor(raisedBy)}">${esc(raisedBy)}</span>
        </div>
      </div>
    </div>`;
    }).join('');
  }).join('');
  // Add manual issue creation button (only when not complete)
  if (state.status !== 'complete') {
    el.innerHTML += `<div style="padding:10px 14px;border-bottom:1px solid var(--border)">
      <button class="btn" style="width:100%;text-align:center" onclick="createIssueFromFile('')">+ 이슈 등록</button>
    </div>`;
  }
  _uiSaveStateToStorage();
}

export function selectIssue(id, { push = true } = {}) {
  state.selectedIssue = id;
  state.selectedFileDiff = null;
  if (state.sessionId && id) uiState._uiSelectedIssueBySession[state.sessionId] = id;
  _uiSaveStateToStorage();

  // Stay on current tab: scroll to the issue within that tab
  if (state.mainTab === 'conversation') {
    _scrollToConvIssue(id);
  } else {
    const issue = state.issues.find(i => i.id === id);
    if (issue && issue.file) {
      window.scrollToFileInChanges(issue.file, issue.line_start || issue.line || 0, issue.id, { push: false });
    } else {
      window.switchMainTab('changes', { push: false });
    }
  }
  if (push) window.router.push({ sessionId: state.sessionId, issueId: id });
}

function _scrollToConvIssue(issueId) {
  const card = document.getElementById('conv-card-' + issueId);
  if (!card) return;
  // Expand if collapsed
  card.classList.remove('conv-collapsed');
  const container = document.getElementById('main-tab-content');
  if (container) {
    const top = card.getBoundingClientRect().top - container.getBoundingClientRect().top + container.scrollTop - 8;
    container.scrollTo({ top, behavior: 'smooth' });
  } else {
    card.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }
  // Flash highlight
  card.classList.remove('issue-item-flash');
  void card.offsetWidth;
  card.classList.add('issue-item-flash');
  setTimeout(() => card.classList.remove('issue-item-flash'), 1050);
}

export function renderIssueFilterOptions() {
  const sel = document.getElementById('filter-agent');
  if (!sel) return;
  const prev = sel.value;
  const savedAgent = uiState._uiSavedFilters?.agent || '';
  const ids = [...new Set(state.agents.map(a => a.model_id).filter(Boolean))].sort();
  sel.innerHTML = `<option value="">전체 에이전트</option>${ids.map(id => `<option value="${esc(id)}">${esc(id)}</option>`).join('')}`;
  if (ids.includes(prev)) {
    sel.value = prev;
  } else if (ids.includes(savedAgent)) {
    sel.value = savedAgent;
  } else {
    sel.value = '';
  }
}

export function toggleIssueGroup(groupKey) {
  state.collapsedIssueGroups[groupKey] = !state.collapsedIssueGroups[groupKey];
  renderIssueList();
}
