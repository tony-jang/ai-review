import state from '../state.js';
import { ACTIVITY_LABELS, ACTIVITY_STALE_MS, MAX_ACTIVITY_HISTORY } from '../constants.js';
import {
  getModelColor,
  esc,
  _escapeAttr,
  formatElapsed,
  _normalizeReviewerAction,
  _reviewerActionClass,
  _reviewerActionLabel,
  _shouldDeferIssueRender,
  _getLatestReviewerOpinion
} from '../utils.js';
import { ensureIssueDisplayNumbers } from './issue-list.js';

let agentTimerInterval = null;
const _agentActivities = {}; // modelId -> { latest: {action, target, ts}, history: [{action, target, ts}] }

export function startAgentTimer() {
  if (agentTimerInterval) return;
  agentTimerInterval = setInterval(() => {
    state.agents.forEach(a => {
      if (a.status === 'reviewing' && a.elapsed_seconds != null) a.elapsed_seconds += 1;
    });
    if (_shouldDeferIssueRender()) return;
    refreshReviewerElapsedTimes();
  }, 1000);
}

export function stopAgentTimer() {
  if (agentTimerInterval) { clearInterval(agentTimerInterval); agentTimerInterval = null; }
}

export function refreshReviewerElapsedTimes() {
  const list = document.getElementById('reviewers-list');
  if (!list) return;
  const nodes = list.querySelectorAll('[data-reviewer-time-model]');
  if (!nodes.length) return;
  const byModel = new Map((state.agents || []).map((a) => [String(a.model_id || ''), a]));
  nodes.forEach((node) => {
    const modelId = String(node.dataset.reviewerTimeModel || '');
    const agent = byModel.get(modelId);
    const elapsed = agent ? formatElapsed(agent.elapsed_seconds) : '';
    if (elapsed) {
      node.textContent = elapsed;
      node.style.display = '';
    } else {
      node.textContent = '';
      node.style.display = 'none';
    }
  });
}

export function _getReviewerIssueEntries(modelId) {
  const key = String(modelId || '');
  const entries = [];
  for (const issue of state.issues || []) {
    const raisedByReviewer = issue?.raised_by === key;
    const latestOpinion = _getLatestReviewerOpinion(issue, key);
    if (latestOpinion) {
      const normalized = _normalizeReviewerAction(latestOpinion.action || 'comment');
      const resolved = (normalized === 'comment' && raisedByReviewer) ? 'raise' : normalized;
      entries.push({ issue, action: resolved, raised: raisedByReviewer, turn: Number(latestOpinion.turn || 0), ts: latestOpinion.timestamp || '' });
      continue;
    }
    if (raisedByReviewer) {
      entries.push({ issue, action: 'raise', raised: true, turn: 0, ts: '' });
    }
  }
  return entries.sort((a, b) => {
    const aNo = Number(state.issueNumberById[a.issue.id] || 0);
    const bNo = Number(state.issueNumberById[b.issue.id] || 0);
    return aNo - bNo;
  });
}

export function _onAgentActivity(data) {
  const { model_id, action, target, timestamp } = data;
  if (!model_id) return;
  if (!_agentActivities[model_id]) {
    _agentActivities[model_id] = { latest: null, history: [] };
  }
  const entry = { action, target, ts: timestamp || new Date().toISOString() };
  _agentActivities[model_id].latest = entry;
  _agentActivities[model_id].history.unshift(entry);
  if (_agentActivities[model_id].history.length > MAX_ACTIVITY_HISTORY) {
    _agentActivities[model_id].history.pop();
  }
  _updateAgentActivityUI(model_id);
  window.updateSummary();
}

function _normalizeActivityAction(action, target) {
  // context with a specific file → treat as file view
  if (action === 'view_context') {
    const file = target.startsWith('context:') ? target.slice(8) : target;
    if (file && file !== 'all') return 'view_file';
  }
  if (action === 'arv_get_context' && target && target !== 'all') return 'arv_get_file';
  return action;
}

export function _formatActivityTarget(action, target) {
  if (action === 'search' && target.startsWith('search:')) return target.slice(7);
  if (action === 'view_tree' && target.startsWith('tree:')) return target.slice(5);
  if (action === 'view_diff' && target.startsWith('diff:')) return target.slice(5);
  if (action === 'view_context' && target.startsWith('context:')) return target.slice(8);
  if (action === 'Grep' && target.startsWith('grep:')) return target.slice(5);
  if (action === 'Glob' && target.startsWith('glob:')) return target.slice(5);
  if (action === 'Bash' && target.startsWith('bash:')) return target.slice(5);
  return target;
}

export function _updateAgentActivityUI(modelId) {
  const el = document.querySelector(`[data-activity-model="${CSS.escape(modelId)}"]`);
  if (!el) return;
  const info = _agentActivities[modelId];
  if (!info || !info.latest) { el.textContent = ''; return; }
  const normAction = _normalizeActivityAction(info.latest.action, info.latest.target);
  const label = ACTIVITY_LABELS[normAction] || normAction;
  const target = _formatActivityTarget(info.latest.action, info.latest.target);
  el.textContent = `${label}: ${target}`;
  el.title = `${label}: ${target}`;
  el.style.display = '';

  // Auto-hide after stale period
  clearTimeout(el._staleTimer);
  el._staleTimer = setTimeout(() => {
    el.textContent = '';
    el.style.display = 'none';
  }, ACTIVITY_STALE_MS);
}

export function _renderActivityTimeline(modelId) {
  const info = _agentActivities[modelId];
  if (!info || !info.history.length) return '';
  const rows = info.history.slice(0, 15).map(e => {
    const normAction = _normalizeActivityAction(e.action, e.target);
    const label = ACTIVITY_LABELS[normAction] || normAction;
    const target = _formatActivityTarget(e.action, e.target);
    const d = new Date(e.ts);
    const time = d.getHours().toString().padStart(2,'0') + ':' + d.getMinutes().toString().padStart(2,'0');
    return `<div class="timeline-entry"><span class="timeline-time">${time}</span><span class="timeline-action">${esc(label)}</span><span class="timeline-target" title="${_escapeAttr(target)}">${esc(target)}</span></div>`;
  }).join('');
  return `<div class="reviewer-timeline">${rows}</div>`;
}

export function _getFileAgentDots(filePath) {
  const dots = [];
  const seen = new Set();
  for (const issue of (state.issues || [])) {
    if (issue.file !== filePath) continue;
    const modelId = issue.raised_by || '';
    if (!modelId || seen.has(modelId)) continue;
    seen.add(modelId);
    dots.push(`<span class="file-agent-dot" style="background:${getModelColor(modelId)}" title="${_escapeAttr(modelId)}"></span>`);
  }
  return dots.length ? `<span class="file-agent-dots">${dots.join('')}</span>` : '';
}

export function _getActivityStats() {
  let totalCalls = 0, filesViewed = new Set(), searches = 0;
  for (const info of Object.values(_agentActivities)) {
    for (const e of info.history) {
      totalCalls++;
      if (e.action === 'view_file' || e.action === 'view_diff' || e.action === 'Read') filesViewed.add(e.target);
      if (e.action === 'search' || e.action === 'Grep') searches++;
    }
  }
  if (!totalCalls) return '';
  return `<span class="activity-stats">API ${totalCalls}회 · 파일 ${filesViewed.size}개 · 검색 ${searches}회</span>`;
}

export function renderAgentPanel() {
  const list = document.getElementById('reviewers-list');
  const countEl = document.getElementById('reviewers-count');
  const prevScrollTop = list ? list.scrollTop : 0;
  const hadScrollable = !!list && list.scrollHeight > list.clientHeight;
  const hasReviewing = state.agents.some(a => a.status === 'reviewing');
  if (hasReviewing) startAgentTimer(); else stopAgentTimer();
  ensureIssueDisplayNumbers();
  if (countEl) countEl.textContent = `${state.agents.length}명`;
  if (!list) return;

  if (!state.agents.length) {
    list.innerHTML = '<div class="reviewers-empty">리뷰어가 없습니다.<br>프리셋을 추가해 시작하세요.</div>';
    return;
  }

  const statusLabel = (status) => {
    if (status === 'reviewing') return '진행중';
    if (status === 'submitted') return '완료';
    if (status === 'failed') return '실패';
    return '대기';
  };

  list.innerHTML = state.agents.map((agent) => {
    const modelId = String(agent.model_id || 'unknown');
    const modelIdEncoded = encodeURIComponent(modelId);
    const modelColor = getModelColor(modelId);
    const elapsed = formatElapsed(agent.elapsed_seconds);
    const status = String(agent.status || 'idle');
    const expanded = !!state.reviewerExpanded[modelId];
    const statusCls = status === 'reviewing'
      ? ' reviewer-status-reviewing'
      : (status === 'submitted' ? ' reviewer-status-submitted' : (status === 'failed' ? ' reviewer-status-failed' : ''));
    const issueEntries = _getReviewerIssueEntries(modelId);
    const raisedEntries = issueEntries.filter(e => e.raised);
    const opinionEntries = issueEntries.filter(e => !e.raised);
    const renderEntryRow = (entry) => {
      const issue = entry.issue || {};
      const issueId = String(issue.id || '');
      const issueIdEncoded = encodeURIComponent(issueId);
      const modelIdForIssueEncoded = encodeURIComponent(modelId);
      const tsEncoded = encodeURIComponent(String(entry.ts || ''));
      const issueNo = Number(state.issueNumberById[issueId] || 0);
      const actionKey = _normalizeReviewerAction(entry.action || 'raise');
      const actionEncoded = encodeURIComponent(actionKey);
      const actionLabel = _reviewerActionLabel(actionKey);
      const actionCls = _reviewerActionClass(actionKey);
      const rowTitle = `#${issueNo} ${String(issue.title || '').trim()}`;
      return `<div class="reviewer-issue-item" onclick="jumpToIssueFromReviewer(decodeURIComponent('${issueIdEncoded}'),decodeURIComponent('${modelIdForIssueEncoded}'),decodeURIComponent('${tsEncoded}'),decodeURIComponent('${actionEncoded}'))" title="${_escapeAttr(`${rowTitle} (${actionLabel})`)}">
        <div class="reviewer-issue-main">
          <span class="reviewer-issue-title">${esc(rowTitle)}</span>
          <span class="reviewer-opinion-badge ${actionCls}">${esc(actionLabel)}</span>
        </div>
      </div>`;
    };
    let issuesHtml = '';
    if (!issueEntries.length) {
      issuesHtml = '<div class="reviewer-issues-empty">아직 남긴 의견이 없습니다.</div>';
    } else {
      if (raisedEntries.length) {
        issuesHtml += `<div class="reviewer-issues-group-label">제기한 이슈 <span class="reviewer-issues-group-count">${raisedEntries.length}</span></div>`;
        issuesHtml += raisedEntries.map(renderEntryRow).join('');
      }
      if (opinionEntries.length) {
        issuesHtml += `<div class="reviewer-issues-group-label">의견 남긴 이슈 <span class="reviewer-issues-group-count">${opinionEntries.length}</span></div>`;
        issuesHtml += opinionEntries.map(renderEntryRow).join('');
      }
    }

    return `<div class="reviewer-card${statusCls}${expanded ? ' expanded' : ''}">
      <div class="reviewer-toggle" role="button" tabindex="0" onclick="toggleReviewerIssues(decodeURIComponent('${modelIdEncoded}'))" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();toggleReviewerIssues(decodeURIComponent('${modelIdEncoded}'));}" title="@${_escapeAttr(modelId)} 의견 보기">
        <div class="reviewer-top">
          <span class="model-dot" style="background:${modelColor}"></span>
          <span class="reviewer-name" style="color:${modelColor}">${esc(modelId)}</span>
          <span class="reviewer-status">${esc(statusLabel(status))}</span>
          <button type="button" class="reviewer-more" onclick="openReviewerChat(decodeURIComponent('${modelIdEncoded}'), event)" title="@${_escapeAttr(modelId)}에게 직접 말걸기">...</button>
          <span class="reviewer-caret">${expanded ? '▾' : '▸'}</span>
        </div>
        <div class="reviewer-meta">
          <span class="reviewer-time" data-reviewer-time-model="${_escapeAttr(modelId)}" ${elapsed ? '' : 'style="display:none"'}>${esc(elapsed || '')}</span>
        </div>
        <div class="reviewer-activity" data-activity-model="${_escapeAttr(modelId)}" style="display:none"></div>
      </div>
      <div class="reviewer-issues">${issuesHtml}</div>
      ${expanded ? _renderActivityTimeline(modelId) : ''}
    </div>`;
  }).join('');

  if (hadScrollable || prevScrollTop > 0) {
    const maxTop = Math.max(0, list.scrollHeight - list.clientHeight);
    list.scrollTop = Math.min(prevScrollTop, maxTop);
  }
}
