import state from '../state.js';
import { getModelColor, esc, _escapeAttr, renderMd, _reviewerActionClass, _getDiscussionOpinions, _issueRangeLabel, _issueLineRange, _isStatusChangeAction, progressBadgeHtml } from '../utils.js';
import { SEVERITY_COLORS, SEVERITY_LABELS, ACTION_LABELS } from '../constants.js';
import { fetchDiff, fetchFileLines } from '../api.js';
import { renderDiffWithFocus, diffContainsLine, renderSourceLines } from '../diff/renderer.js';
import { highlightIssueRange, changesReady } from './changes.js';
import { ensureIssueDisplayNumbers } from './issue-list.js';

/* ── Mini-diff cache ──────────────────────────────────── */
const _miniDiffCache = new Map();  // issue.id → rendered HTML
export function clearMiniDiffCache() { _miniDiffCache.clear(); }

/* ── Helpers ───────────────────────────────────────────── */

function _modelInitial(modelId) {
  if (!modelId) return '?';
  // Take first char of last segment (e.g. "gpt-4o" → "G", "claude-opus" → "C")
  const parts = modelId.split(/[-_/]/);
  return (parts[0] || '?')[0].toUpperCase();
}

function _findAgent(modelId) {
  return state.agents.find(a => a.model_id === modelId);
}

function _renderAvatar(modelId, color, isLast) {
  let html = '<div class="conv-tl-avatar-col">';
  html += `<div class="conv-tl-avatar" style="background:${color}">${_modelInitial(modelId)}</div>`;
  if (!isLast) html += '<div class="conv-tl-line"></div>';
  html += '</div>';
  return html;
}

function _renderCardHeader(modelId, color, agent, timeStr, extraHtml, { toggleIssueId, isReporter, issueDisplayNo } = {}) {
  const onclick = toggleIssueId ? ` onclick="toggleConvIssue('${_escapeAttr(toggleIssueId)}')"` : '';
  let html = `<div class="conv-tl-card-header"${onclick}>`;
  html += `<span class="tl-agent-name" style="color:${color}">${esc(modelId)}</span>`;
  html += `<span class="tl-bot-badge${isReporter ? ' reporter-badge' : ''}">Reviewer${isReporter ? ' · Reporter' : ''}</span>`;
  if (agent?.role) html += `<span class="tl-role">${esc(agent.role)}</span>`;
  if (extraHtml) html += `<span class="tl-extra">${extraHtml}</span>`;
  html += `<span class="tl-time">${esc(timeStr)}</span>`;
  if (issueDisplayNo) html += `<button class="issue-menu-btn" onclick="event.stopPropagation();showIssueMenu(event,'${_escapeAttr(state.sessionId)}',${issueDisplayNo})" title="이슈 메뉴">⋯</button>`;
  if (toggleIssueId) html += '<span class="conv-issue-caret">▾</span>';
  html += '</div>';
  return html;
}

function _renderReviewSummaryCard(review, color, agent, isLast) {
  const timeStr = new Date(review.submitted_at).toLocaleString();
  const turnLabel = review.turn > 0 ? `Turn ${review.turn}` : '';
  const turnHtml = turnLabel ? `<span style="font-size:11px;color:var(--text-muted)">${esc(turnLabel)}</span>` : '';

  let html = '<div class="conv-tl-item">';
  html += _renderAvatar(review.model_id, color, isLast);
  html += '<div class="conv-tl-content">';
  html += '<div class="conv-tl-card">';
  html += _renderCardHeader(review.model_id, color, agent, timeStr, turnHtml);
  html += '<div class="conv-tl-card-body">';
  if (review.summary) {
    html += `<div class="tl-summary">${renderMd(review.summary)}</div>`;
  }
  html += `<div class="tl-issue-count">${review.issue_count}개 이슈 제기</div>`;
  html += '</div></div></div></div>';
  return html;
}

function _renderIssueCard(issue, review, color, agent, isLast) {
  const sev = issue.severity || 'low';
  const sevColor = SEVERITY_COLORS[sev] || SEVERITY_COLORS.low;
  const sevLabel = SEVERITY_LABELS[sev] || sev;
  const range = _issueRangeLabel(issue);
  const fileLabel = issue.file ? (issue.file.split('/').pop() + (range ? ':' + range : '')) : '';
  const consensusIcon = issue.consensus ? ' ✅' : '';
  const displayNo = state.issueNumberById[issue.id] || 0;
  const timeStr = new Date(review.submitted_at).toLocaleString();

  let html = '<div class="conv-tl-item">';
  html += _renderAvatar(review.model_id, color, isLast);
  html += '<div class="conv-tl-content">';
  const resolved = issue.progress_status === 'completed' || issue.progress_status === 'wont_fix';
  html += `<div class="conv-tl-card tl-issue-card${resolved ? ' conv-collapsed' : ''}" id="conv-card-${issue.id}" style="border-left-color:${sevColor}">`;
  html += _renderCardHeader(review.model_id, color, agent, timeStr, progressBadgeHtml(issue.progress_status), { toggleIssueId: issue.id, isReporter: true, issueDisplayNo: displayNo });
  html += '<div class="conv-tl-card-body">';

  // Issue head: severity + number + title + goto
  html += '<div class="tl-issue-head">';
  html += `<span class="tl-sev-badge" style="background:${sevColor}20;color:${sevColor}">${esc(sevLabel)}</span>`;
  html += progressBadgeHtml(issue.progress_status);
  html += `<span class="tl-issue-no">#${displayNo}</span>`;
  html += `<span class="tl-issue-title">${esc(issue.title || issue.description?.slice(0, 80) || 'Untitled')}${consensusIcon}</span>`;
  if (issue.file) html += `<button class="tl-goto-btn" title="Changes에서 보기" onclick="event.stopPropagation();scrollToFileInChanges('${_escapeAttr(issue.file || '')}', ${issue.line_start || issue.line || 0}, '${_escapeAttr(issue.id)}')">Changes →</button>`;
  html += '</div>';

  // File label
  if (fileLabel) html += `<span class="tl-file-label" data-path="${_escapeAttr(issue.file)}" onclick="onFileTreeFileContextMenu(event, this)">${esc(fileLabel)}</span>`;

  // Description
  if (issue.description) {
    html += `<div class="diff-inline-thread-desc">${renderMd(issue.description)}</div>`;
  }

  // Mini diff (cached or async)
  if (issue.file) {
    const cached = _miniDiffCache.get(issue.id) || '';
    html += `<div class="conv-issue-card-diff" id="conv-diff-${issue.id}">${cached}</div>`;
  }

  // Thread opinions
  const discussions = _getDiscussionOpinions(issue);
  if (discussions.length) {
    html += '<div class="diff-inline-thread-opinions">';
    discussions.forEach(op => {
      if (_isStatusChangeAction(op.action)) {
        const opColor = getModelColor(op.model_id || '');
        const _isAuthor = op.model_id !== issue.raised_by;
        const _roleBadge = _isAuthor
          ? '<span class="action-badge" style="background:rgba(245,158,11,0.12);color:#F59E0B">Author</span>'
          : '<span class="action-badge" style="background:rgba(99,102,241,0.12);color:#818CF8">Reviewer</span>';
        html += `<div class="status-change-log">
          <span class="status-change-arrow">&rarr;</span>
          <span class="model-dot" style="background:${opColor};width:8px;height:8px"></span>
          <span class="status-change-author" style="color:${opColor}">${esc(op.model_id || '')}</span>
          ${_roleBadge}
          ${op.status_value
            ? (op.previous_status
              ? `가 상태를 ${progressBadgeHtml(op.previous_status)} 에서 ${progressBadgeHtml(op.status_value)} 로 변경했습니다.`
              : `가 상태를 ${progressBadgeHtml(op.status_value)} (으)로 변경했습니다.`)
            : esc(op.reasoning || '')}
        </div>`;
        return;
      }
      const opColor = getModelColor(op.model_id || '');
      const actionLabel = ACTION_LABELS[op.action] || op.action;
      const actionClass = _reviewerActionClass(op.action);
      const isOpReporter = op.model_id === issue.raised_by;
      html += '<div class="diff-inline-opinion-block">';
      html += '<div class="diff-inline-opinion-header">';
      html += `<div class="diff-inline-avatar diff-inline-avatar-sm" style="background:${opColor}">${_modelInitial(op.model_id)}</div>`;
      html += `<span class="diff-inline-agent-name" style="color:${opColor}">${esc(op.model_id || '')}</span>`;
      html += `<span class="diff-inline-bot-badge${isOpReporter ? ' reporter-badge' : ''}">Reviewer${isOpReporter ? ' · Reporter' : ''}</span>`;
      html += `<span class="diff-inline-opinion-action ${actionClass}">${esc(actionLabel)}</span>`;
      html += '</div>';
      html += `<div class="diff-inline-opinion-body">${renderMd(op.reasoning || '')}</div>`;
      html += '</div>';
    });
    html += '</div>';
  }

  html += '</div></div></div></div>';
  return html;
}

export function toggleConvIssue(issueId) {
  const card = document.getElementById('conv-card-' + issueId);
  if (!card) return;
  card.classList.toggle('conv-collapsed');
}

/* ── Main render ───────────────────────────────────────── */

export function renderConversationTab(container) {
  if (!state.sessionId) {
    container.innerHTML = '<div class="empty-state"><div class="icon">💬</div><div class="message">리뷰 세션을 시작하세요</div></div>';
    return;
  }
  let html = '';

  // 1. Description block
  const ctx = state.implementationContext;
  if (ctx && (ctx.summary || (ctx.decisions && ctx.decisions.length))) {
    html += '<div class="conv-description">';
    html += '<h3>Implementation Context</h3>';
    if (ctx.summary) html += `<div class="md-content">${renderMd(ctx.summary)}</div>`;
    if (ctx.decisions && ctx.decisions.length) {
      html += '<ul class="conv-decisions">';
      ctx.decisions.forEach(d => { html += `<li>${esc(d)}</li>`; });
      html += '</ul>';
    }
    if (ctx.tradeoffs && ctx.tradeoffs.length) {
      html += '<div style="margin-top:8px;font-size:12px;color:var(--text-muted)"><strong>Tradeoffs:</strong></div>';
      html += '<ul class="conv-decisions">';
      ctx.tradeoffs.forEach(t => { html += `<li>${esc(t)}</li>`; });
      html += '</ul>';
    }
    if (ctx.submitted_by) {
      const timeStr = ctx.submitted_at ? new Date(ctx.submitted_at).toLocaleString() : '';
      html += `<div style="margin-top:8px;font-size:11px;color:var(--text-muted)">by ${esc(ctx.submitted_by)}${timeStr ? ' · ' + timeStr : ''}</div>`;
    }
    html += '</div>';
  }

  // 2. Review timeline
  const reviews = (state.reviews || []).slice().sort((a, b) => new Date(a.submitted_at) - new Date(b.submitted_at));
  if (!reviews.length && !ctx) {
    container.innerHTML = '<div class="empty-state"><div class="icon">💬</div><div class="message">아직 리뷰가 없습니다</div><div class="hint">에이전트가 리뷰를 제출하면 타임라인이 표시됩니다</div></div>';
    return;
  }

  ensureIssueDisplayNumbers();

  if (reviews.length) {
    // Build flat event list
    const events = [];
    reviews.forEach(review => {
      events.push({ type: 'review', review });
      const reviewerIssues = state.issues.filter(i => i.raised_by === review.model_id && i.turn === review.turn);
      reviewerIssues.forEach(issue => {
        events.push({ type: 'issue', issue, review });
      });
    });

    html += '<div class="conv-timeline">';
    events.forEach((evt, idx) => {
      const isLast = idx === events.length - 1;
      const color = getModelColor(evt.review.model_id);
      const agent = _findAgent(evt.review.model_id);

      if (evt.type === 'review') {
        html += _renderReviewSummaryCard(evt.review, color, agent, isLast);
      } else {
        html += _renderIssueCard(evt.issue, evt.review, color, agent, isLast);
      }
    });
    html += '</div>';
  }

  container.innerHTML = html;

  // Async load mini diffs for issue cards (skip already cached)
  const allReviewerIssues = state.issues.filter(i => reviews.some(r => r.model_id === i.raised_by));
  allReviewerIssues.forEach(async (issue) => {
    if (!issue.file) return;
    if (_miniDiffCache.has(issue.id)) return;
    const diffEl = document.getElementById(`conv-diff-${issue.id}`);
    if (!diffEl) return;
    const diffContent = await fetchDiff(issue.file);
    const target = _issueLineRange(issue);

    const _setDiff = (html) => {
      _miniDiffCache.set(issue.id, html);
      // Element may have been detached by re-render; find fresh reference
      const el = document.getElementById(`conv-diff-${issue.id}`);
      if (el) el.innerHTML = html;
    };

    // If diff doesn't contain the target lines, fetch source directly
    if (target.start !== null && (!diffContent || !diffContainsLine(diffContent, target.start))) {
      const ctx = 3;
      const start = Math.max(1, target.start - ctx);
      const end = (target.end ?? target.start) + ctx;
      const data = await fetchFileLines(issue.file, start, end);
      if (data) {
        _setDiff(renderSourceLines(data, issue));
        return;
      }
    }

    if (diffContent) {
      _setDiff(renderDiffWithFocus(diffContent, issue, 3));
    }
  });
}

export function scrollToFileInChanges(filePath, lineStart, issueId, { push = true } = {}) {
  const needsSwitch = state.mainTab !== 'changes';

  if (needsSwitch) {
    window.switchMainTab('changes', { push });
  }

  // Wait for the render to finish (resolves instantly if already rendered)
  changesReady().then(() => {
    requestAnimationFrame(() => {
      if (issueId) highlightIssueRange(issueId);

      // Prefer scrolling directly to the inline comment
      const commentEl = issueId ? document.getElementById(`inline-issue-${issueId}`) : null;
      const scrollContainer = document.getElementById('main-tab-content');
      const target = commentEl || document.querySelector(`.changes-diff-section[data-file="${CSS.escape(filePath)}"]`);
      if (target && scrollContainer) {
        // Account for sticky diff header height
        const stickyHeader = target.closest('.changes-diff-section')?.querySelector('.changes-diff-header');
        const offset = stickyHeader ? stickyHeader.offsetHeight + 8 : 8;
        const targetTop = target.getBoundingClientRect().top - scrollContainer.getBoundingClientRect().top + scrollContainer.scrollTop - offset;
        scrollContainer.scrollTo({ top: targetTop, behavior: 'smooth' });
      } else if (target) {
        target.scrollIntoView({ behavior: 'smooth', block: 'start' });
      }
    });
  });
}
