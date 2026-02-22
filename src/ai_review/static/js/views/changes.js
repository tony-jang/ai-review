import state from '../state.js';
import { esc, _escapeAttr, renderMd, getModelColor, _reviewerActionClass, _getDiscussionOpinions, _issueLineRange, _isStatusChangeAction, progressBadgeHtml } from '../utils.js';
import { SEVERITY_COLORS, SEVERITY_LABELS, ACTION_LABELS } from '../constants.js';
import { fetchDiff } from '../api.js';
import { parseDiffLines } from '../diff/parser.js';
import { _guessDiffLanguage } from '../diff/highlighter.js';
import { _renderDiffRows } from '../diff/renderer.js';
import { _buildFileTree, _renderFileTreeNode, _sortedFileTreeChildren, _passesFileFilter, _syncFileFilterState } from './file-panel.js';
import { ensureIssueDisplayNumbers } from './issue-list.js';

let _stickyBound = false;
let _renderGeneration = 0;
let _renderPromise = Promise.resolve();

/** Returns a promise that resolves when the current/last render completes. */
export function changesReady() {
  return _renderPromise;
}

export async function renderChangesTab(container) {
  let resolve;
  _renderPromise = new Promise(r => { resolve = r; });
  const gen = ++_renderGeneration;
  if (!state.sessionId || !state.files.length) {
    container.innerHTML = '<div class="empty-state"><div class="icon">📝</div><div class="message">변경된 파일이 없습니다</div></div>';
    resolve();
    return;
  }

  let html = '';

  // Diff sections placeholder
  state.files.forEach(f => {
    html += `<div class="changes-diff-section" data-file="${_escapeAttr(f.path)}" id="changes-section-${CSS.escape(f.path).replace(/[^a-zA-Z0-9_-]/g, '_')}">`;
    html += `<div class="changes-diff-header">`;
    html += `<span class="file-path">${esc(f.path)}</span>`;
    const add = f.additions > 0 ? `<span class="add" style="font-size:11px;color:var(--severity-dismissed)">+${f.additions}</span>` : '';
    const del = f.deletions > 0 ? `<span class="del" style="font-size:11px;color:var(--severity-critical)">-${f.deletions}</span>` : '';
    html += `<span style="display:flex;gap:6px;margin-left:auto">${add} ${del}</span>`;
    html += `<button class="diff-menu-btn" data-path="${_escapeAttr(f.path)}" onclick="event.stopPropagation(); onFileTreeFileContextMenu(event, this)" title="파일 메뉴">⋯</button>`;
    html += '</div>';
    html += `<div class="changes-diff-body" id="changes-diff-${CSS.escape(f.path).replace(/[^a-zA-Z0-9_-]/g, '_')}">`;
    html += '<div class="diff-loading" style="padding:12px;color:var(--text-muted);font-size:12px">로딩 중...</div>';
    html += '</div>';
    html += '</div>';
  });

  container.innerHTML = html;

  // Bind sticky detection once, run immediately + after diffs load
  _bindStickyHeaderDetection(container);
  _updateStickyHeaders();

  // 3. Fetch all diffs in parallel, then render
  const diffs = await Promise.all(state.files.map(f => fetchDiff(f.path)));
  if (gen !== _renderGeneration) { resolve(); return; } // stale render, skip DOM update
  ensureIssueDisplayNumbers();
  for (let i = 0; i < state.files.length; i++) {
    const f = state.files[i];
    const diffBody = document.getElementById(`changes-diff-${CSS.escape(f.path).replace(/[^a-zA-Z0-9_-]/g, '_')}`);
    if (!diffBody) continue;
    const diffContent = diffs[i];
    if (!diffContent) {
      diffBody.innerHTML = '<div class="diff-loading" style="padding:12px;color:var(--text-muted);font-size:12px">변경 내역 없음</div>';
      continue;
    }
    const lines = parseDiffLines(diffContent);
    if (!lines.length) {
      diffBody.innerHTML = '<div class="diff-loading" style="padding:12px;color:var(--text-muted);font-size:12px">변경 없음</div>';
      continue;
    }
    const language = _guessDiffLanguage(f.path);
    const enableHighlight = !!(language && lines.length <= 1200);
    const diffHtml = _renderDiffRows(lines, null, language, enableHighlight, f.path);
    diffBody.innerHTML = diffHtml;
    _insertInlineComments(diffBody, f.path);
  }
  _updateStickyHeaders();
  resolve();
}

export function scrollToChangesFile(filePath) {
  state.changesActiveFile = filePath || null;
  renderChangesSidebar();
  const section = document.querySelector(`.changes-diff-section[data-file="${CSS.escape(filePath)}"]`);
  if (section) section.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

export function renderChangesSidebar() {
  const listEl = document.getElementById('changes-sidebar-list');
  const countEl = document.getElementById('changes-sidebar-count');
  if (!listEl) return;
  const facets = _syncFileFilterState(state.files);
  const filteredFiles = state.files.filter(_passesFileFilter);
  const countText = filteredFiles.length === state.files.length
    ? `${state.files.length}개`
    : `${filteredFiles.length}/${state.files.length}개`;
  if (countEl) countEl.textContent = countText;
  // Sync filter input
  const input = document.getElementById('changes-file-filter-input');
  if (input && input.value !== state.fileFilterQuery) input.value = state.fileFilterQuery;
  // Build tree
  const tree = _buildFileTree(filteredFiles);
  const html = _sortedFileTreeChildren(tree).map(node => _renderFileTreeNode(node, 0)).join('');
  if (!html) {
    listEl.innerHTML = '<div class="file-tree-empty">필터 조건에 맞는 파일이 없습니다.</div>';
    return;
  }
  listEl.innerHTML = `<div class="file-tree-canvas">${html}</div>`;
}

export function onChangesFileFilterInput(value) {
  state.fileFilterQuery = String(value || '');
  renderChangesSidebar();
}

function _updateStickyHeaders() {
  const c = document.getElementById('main-tab-content');
  if (!c) return;
  const top = c.getBoundingClientRect().top;
  c.querySelectorAll('.changes-diff-header').forEach(h => {
    h.classList.toggle('stuck', h.getBoundingClientRect().top <= top + 1);
  });
}

function _bindStickyHeaderDetection(container) {
  if (_stickyBound) return;
  container.addEventListener('scroll', _updateStickyHeaders, { passive: true });
  _stickyBound = true;
}

function _insertInlineComments(diffBody, filePath) {
  // Collect issues for this file
  const fileIssues = state.issues.filter(i => i.file === filePath);
  if (!fileIssues.length) return;

  // Group issues by line_end (or line_start or line)
  const issuesByLine = {};
  const noLineIssues = [];
  fileIssues.forEach(issue => {
    const r = _issueLineRange(issue);
    const lineKey = r.end || r.start;
    if (lineKey === null) { noLineIssues.push(issue); return; }
    if (!issuesByLine[lineKey]) issuesByLine[lineKey] = [];
    issuesByLine[lineKey].push(issue);
  });

  // Find diff table rows and insert comment rows after matching lines
  const table = diffBody.querySelector('table');
  const outOfRangeIssues = [...noLineIssues];

  if (table) {
    const rows = table.querySelectorAll('tr');
    const lineKeys = Object.keys(issuesByLine).map(Number).sort((a, b) => b - a);
    const isSplit = table.classList.contains('diff-split-table');
    for (const lineNo of lineKeys) {
      let targetRow = null;
      for (let i = rows.length - 1; i >= 0; i--) {
        const row = rows[i];
        if (row.classList.contains('diff-hunk') || row.classList.contains('diff-inline-comment')) continue;
        let newLineText = '';
        if (isSplit) {
          const cell = row.querySelector('.diff-split-new-num');
          if (cell) newLineText = cell.textContent.trim();
        } else {
          const cells = row.querySelectorAll('.diff-line-num');
          if (cells.length >= 2) newLineText = cells[1].textContent.trim();
        }
        if (newLineText && parseInt(newLineText) === lineNo) {
          targetRow = row;
          break;
        }
      }

      if (!targetRow) {
        outOfRangeIssues.push(...issuesByLine[lineNo]);
        continue;
      }
      const issues = issuesByLine[lineNo];
      const commentRow = document.createElement('tr');
      commentRow.className = 'diff-inline-comment';
      const td = document.createElement('td');
      td.setAttribute('colspan', isSplit ? '4' : '3');
      td.innerHTML = _renderInlineCommentThread(issues);
      commentRow.appendChild(td);
      targetRow.after(commentRow);
    }
  } else {
    // No table at all — all issues are out of range
    for (const issues of Object.values(issuesByLine)) outOfRangeIssues.push(...issues);
  }

  // Render out-of-range issues above the diff body
  if (outOfRangeIssues.length) {
    const section = document.createElement('div');
    section.className = 'diff-out-of-range';
    section.innerHTML = _renderOutOfRangeIssues(outOfRangeIssues);
    diffBody.parentElement.insertBefore(section, diffBody);
  }

  // Bind agent header click to collapse/expand
  const parent = diffBody.parentElement;
  parent.querySelectorAll('.diff-inline-thread-agent').forEach(agent => {
    agent.addEventListener('click', () => {
      const item = agent.closest('.diff-inline-thread-item');
      if (item) item.classList.toggle('collapsed');
    });
  });

  // Bind range toggle buttons
  parent.querySelectorAll('.diff-range-toggle-btn[data-issue-id]').forEach(btn => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      const id = btn.dataset.issueId;
      const isActive = btn.classList.contains('active');
      highlightIssueRange(isActive ? null : id);
    });
  });
}

function _modelInitial(modelId) {
  if (!modelId) return '?';
  return (modelId.split(/[-_/]/)[0] || '?')[0].toUpperCase();
}

function _findReviewTime(issue) {
  const review = (state.reviews || []).find(r => r.model_id === issue.raised_by && r.turn === issue.turn);
  return review?.submitted_at ? new Date(review.submitted_at).toLocaleString() : '';
}

function _renderIssueComment(issue, extraLineHtml) {
  const sev = issue.severity || 'low';
  const sevColor = SEVERITY_COLORS[sev] || SEVERITY_COLORS.low;
  const sevLabel = SEVERITY_LABELS[sev] || sev;
  const raisedBy = issue.raised_by || 'unknown';
  const raisedColor = getModelColor(raisedBy);
  const consensusIcon = issue.consensus ? ' ✅' : '';
  const timeStr = _findReviewTime(issue);
  const displayNo = state.issueNumberById[issue.id] || 0;

  const resolved = issue.progress_status && issue.progress_status !== 'reported';
  let html = `<div class="diff-inline-thread-item${resolved ? ' collapsed' : ''}" id="inline-issue-${_escapeAttr(issue.id)}" data-issue-id="${_escapeAttr(issue.id)}" style="border-left:3px solid ${sevColor}">`;
  // Agent header
  html += '<div class="diff-inline-thread-agent">';
  html += `<div class="diff-inline-avatar" style="background:${raisedColor}">${_modelInitial(raisedBy)}</div>`;
  html += `<span class="diff-inline-agent-name" style="color:${raisedColor}">${esc(raisedBy)}</span>`;
  html += '<span class="diff-inline-bot-badge">Reviewer</span>';
  if (timeStr) html += `<span class="diff-inline-time">${esc(timeStr)}</span>`;
  html += '</div>';
  // Severity + number + title
  html += '<div class="diff-inline-thread-header">';
  html += `<span class="diff-inline-thread-severity" style="background:${sevColor}20;color:${sevColor}">${esc(sevLabel)}</span>`;
  html += progressBadgeHtml(issue.progress_status);
  if (displayNo) html += `<span class="tl-issue-no">#${displayNo}</span>`;
  if (extraLineHtml) html += extraLineHtml;
  if (issue.file && _issueLineRange(issue).start !== null) {
    html += `<button class="diff-range-toggle-btn" data-issue-id="${_escapeAttr(issue.id)}">범위</button>`;
  }
  html += `<span class="diff-inline-thread-title">${esc(issue.title || issue.description?.slice(0, 80) || 'Untitled')}${consensusIcon}</span>`;
  html += '</div>';
  // Description
  if (issue.description) {
    html += `<div class="diff-inline-thread-desc">${renderMd(issue.description)}</div>`;
  }
  // Thread opinions
  const discussions = _getDiscussionOpinions(issue);
  if (discussions.length) {
    html += '<div class="diff-inline-thread-opinions">';
    discussions.forEach(op => {
      if (_isStatusChangeAction(op.action)) {
        const opColor = getModelColor(op.model_id || '');
        html += `<div class="status-change-log">
          <span class="status-change-arrow">&rarr;</span>
          <span class="model-dot" style="background:${opColor};width:8px;height:8px"></span>
          <span>${esc(op.reasoning || '')}</span>
        </div>`;
        return;
      }
      const opColor = getModelColor(op.model_id || '');
      const actionLabel = ACTION_LABELS[op.action] || op.action;
      const actionClass = _reviewerActionClass(op.action);
      html += '<div class="diff-inline-opinion-block">';
      html += '<div class="diff-inline-opinion-header">';
      html += `<div class="diff-inline-avatar diff-inline-avatar-sm" style="background:${opColor}">${_modelInitial(op.model_id)}</div>`;
      html += `<span class="diff-inline-agent-name" style="color:${opColor}">${esc(op.model_id || '')}</span>`;
      html += '<span class="diff-inline-bot-badge">Reviewer</span>';
      html += `<span class="diff-inline-opinion-action ${actionClass}">${esc(actionLabel)}</span>`;
      html += '</div>';
      html += `<div class="diff-inline-opinion-body">${renderMd(op.reasoning || '')}</div>`;
      html += '</div>';
    });
    html += '</div>';
  }
  html += '</div>';
  return html;
}

function _renderOutOfRangeIssues(issues) {
  let html = '<div class="diff-out-of-range-header">Out of diff range</div>';
  html += '<div class="diff-inline-thread">';
  issues.forEach(issue => {
    const r = _issueLineRange(issue);
    const lineInfo = r.start ? (r.end && r.end !== r.start ? `L${r.start}–${r.end}` : `L${r.start}`) : '';
    const lineHtml = lineInfo ? `<span class="diff-out-of-range-line">${esc(lineInfo)}</span>` : '';
    html += _renderIssueComment(issue, lineHtml);
  });
  html += '</div>';
  return html;
}

function _renderInlineCommentThread(issues) {
  let html = '<div class="diff-inline-thread">';
  issues.forEach(issue => {
    const r = _issueLineRange(issue);
    const lineInfo = r.start ? (r.end && r.end !== r.start ? `L${r.start}\u2013${r.end}` : `L${r.start}`) : '';
    const lineHtml = lineInfo ? `<span class="diff-out-of-range-line">${esc(lineInfo)}</span>` : '';
    html += _renderIssueComment(issue, lineHtml);
  });
  html += '</div>';
  return html;
}

export function highlightIssueRange(issueId) {
  // Clear previous highlights
  document.querySelectorAll('.diff-issue-marker').forEach(el => el.classList.remove('diff-issue-marker'));
  document.querySelectorAll('.diff-inline-thread-item.active').forEach(el => el.classList.remove('active'));
  document.querySelectorAll('.diff-range-toggle-btn.active').forEach(el => el.classList.remove('active'));

  if (!issueId) return;
  const issue = state.issues.find(i => i.id === issueId);
  if (!issue) return;

  // Highlight the clicked thread item and its toggle button
  const threadItem = document.getElementById(`inline-issue-${issueId}`);
  if (threadItem) {
    threadItem.classList.add('active');
    const btn = threadItem.querySelector('.diff-range-toggle-btn');
    if (btn) btn.classList.add('active');
  }

  const r = _issueLineRange(issue);
  if (r.start === null) return;
  const end = r.end ?? r.start;

  // Find the parent diff section
  const section = threadItem?.closest('.changes-diff-section');
  if (!section) return;
  const table = section.querySelector('table');
  if (!table) return;

  const isSplit = table.classList.contains('diff-split-table');
  table.querySelectorAll('tr').forEach(row => {
    if (row.classList.contains('diff-hunk') || row.classList.contains('diff-inline-comment')) return;
    let newLineText = '';
    if (isSplit) {
      const cell = row.querySelector('.diff-split-new-num');
      if (cell) newLineText = cell.textContent.trim();
    } else {
      const cells = row.querySelectorAll('.diff-line-num');
      if (cells.length >= 2) newLineText = cells[1].textContent.trim();
    }
    if (!newLineText) return;
    const lineNo = parseInt(newLineText);
    if (lineNo >= r.start && lineNo <= end) {
      if (isSplit) {
        row.querySelectorAll('td').forEach(td => td.classList.add('diff-issue-marker'));
      } else {
        row.classList.add('diff-issue-marker');
      }
    }
  });
}

export function toggleSection(id, btnId) {
  const body = document.getElementById(id);
  const btn = document.getElementById(btnId);
  if (!body || !btn) return;
  body.classList.toggle('collapsed');
  btn.classList.toggle('collapsed');
}
