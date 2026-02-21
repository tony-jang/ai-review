import state from '../state.js';
import { getModelColor, esc, _escapeAttr, renderMd, _reviewerActionClass, _getDiscussionOpinions, _issueRangeLabel, _issueLineRange } from '../utils.js';
import { SEVERITY_COLORS, SEVERITY_LABELS, ACTION_LABELS } from '../constants.js';
import { fetchDiff, fetchFileLines } from '../api.js';
import { renderDiffWithFocus, diffContainsLine, renderSourceLines } from '../diff/renderer.js';
import { highlightIssueRange } from './changes.js';
import { ensureIssueDisplayNumbers } from './issue-list.js';

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
    html += '<div class="conv-timeline">';
    reviews.forEach(review => {
      const color = getModelColor(review.model_id);
      const modelLabel = review.model_id;
      const timeStr = new Date(review.submitted_at).toLocaleString();
      const turnLabel = review.turn > 0 ? `Turn ${review.turn}` : '';

      html += '<div class="conv-review-entry">';
      // Header
      html += '<div class="conv-review-header">';
      html += `<span class="conv-review-model-dot" style="background:${color}"></span>`;
      html += `<span class="conv-review-model-name" style="color:${color}">${esc(modelLabel)}</span>`;
      html += `<span class="conv-review-meta">${turnLabel ? esc(turnLabel) + ' · ' : ''}${esc(timeStr)} · ${review.issue_count}개 이슈</span>`;
      html += '</div>';
      // Summary
      if (review.summary) {
        html += `<div class="conv-review-summary">${renderMd(review.summary)}</div>`;
      }
      // Issue cards for this reviewer
      const reviewerIssues = state.issues.filter(i => i.raised_by === review.model_id && i.turn === review.turn);
      if (reviewerIssues.length) {
        html += '<div class="conv-issue-cards">';
        reviewerIssues.forEach(issue => {
          const sev = issue.severity || 'low';
          const sevColor = SEVERITY_COLORS[sev] || SEVERITY_COLORS.low;
          const sevLabel = SEVERITY_LABELS[sev] || sev;
          const range = _issueRangeLabel(issue);
          const fileLabel = issue.file ? (issue.file.split('/').pop() + (range ? ':' + range : '')) : '';
          const consensusIcon = issue.consensus ? ' ✅' : '';
          const displayNo = state.issueNumberById[issue.id] || 0;

          html += `<div class="conv-issue-card">`;
          html += '<div class="conv-issue-card-header">';
          html += `<span class="conv-issue-card-severity" style="background:${sevColor}20;color:${sevColor}">${esc(sevLabel)}</span>`;
          html += `<span class="conv-issue-card-number">#${displayNo}</span>`;
          html += `<span class="conv-issue-card-title">${esc(issue.title || issue.description?.slice(0, 80) || 'Untitled')}${consensusIcon}</span>`;
          if (issue.file) html += `<button class="conv-issue-card-goto" title="Changes에서 보기" onclick="event.stopPropagation();scrollToFileInChanges('${_escapeAttr(issue.file || '')}', ${issue.line_start || issue.line || 0}, '${_escapeAttr(issue.id)}')">Changes →</button>`;
          html += '</div>';
          if (fileLabel) html += `<span class="conv-issue-card-file" data-path="${_escapeAttr(issue.file)}" onclick="onFileTreeFileContextMenu(event, this)">${esc(fileLabel)}</span>`;
          // Description
          if (issue.description) {
            html += `<div class="diff-inline-thread-desc">${renderMd(issue.description)}</div>`;
          }
          // Mini diff (async load)
          if (issue.file) {
            html += `<div class="conv-issue-card-diff" id="conv-diff-${issue.id}"></div>`;
          }
          // Thread opinions
          const discussions = _getDiscussionOpinions(issue);
          if (discussions.length) {
            html += '<div class="conv-issue-card-thread">';
            discussions.forEach(op => {
              const opColor = getModelColor(op.model_id || '');
              const actionLabel = ACTION_LABELS[op.action] || op.action;
              const actionClass = _reviewerActionClass(op.action);
              html += '<div class="diff-inline-opinion">';
              html += `<span class="diff-inline-opinion-dot" style="background:${opColor}"></span>`;
              html += `<span class="diff-inline-opinion-model">${esc(op.model_id || '')}</span>`;
              html += `<span class="diff-inline-opinion-action ${actionClass}">${esc(actionLabel)}</span>`;
              html += `<span class="diff-inline-opinion-text">${renderMd(op.reasoning || '')}</span>`;
              html += '</div>';
            });
            html += '</div>';
          }
          html += '</div>';
        });
        html += '</div>';
      }
      html += '</div>';
    });
    html += '</div>';
  }

  container.innerHTML = html;

  // Async load mini diffs for issue cards
  const allReviewerIssues = state.issues.filter(i => reviews.some(r => r.model_id === i.raised_by));
  allReviewerIssues.forEach(async (issue) => {
    if (!issue.file) return;
    const diffEl = document.getElementById(`conv-diff-${issue.id}`);
    if (!diffEl) return;
    const diffContent = await fetchDiff(issue.file);
    const target = _issueLineRange(issue);

    // If diff doesn't contain the target lines, fetch source directly
    if (diffContent && target.start !== null && !diffContainsLine(diffContent, target.start)) {
      const ctx = 3;
      const start = Math.max(1, target.start - ctx);
      const end = (target.end ?? target.start) + ctx;
      const data = await fetchFileLines(issue.file, start, end);
      if (data) {
        diffEl.innerHTML = renderSourceLines(data, issue);
        return;
      }
    }

    if (diffContent) {
      diffEl.innerHTML = renderDiffWithFocus(diffContent, issue, 3);
    }
  });
}

export function scrollToFileInChanges(filePath, lineStart, issueId) {
  window.switchMainTab('changes');
  // After tab switch, scroll to the file section
  requestAnimationFrame(() => {
    const anchor = document.querySelector(`.changes-diff-section[data-file="${CSS.escape(filePath)}"]`);
    if (anchor) {
      anchor.scrollIntoView({ behavior: 'smooth', block: 'start' });
      // Highlight the relevant inline comment if issueId is provided
      if (issueId) {
        setTimeout(() => {
          highlightIssueRange(issueId);
          const commentEl = document.getElementById(`inline-issue-${issueId}`);
          if (commentEl) {
            commentEl.scrollIntoView({ behavior: 'smooth', block: 'center' });
          }
        }, 300);
      }
    }
  });
}
