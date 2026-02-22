import { MODEL_COLORS, SEVERITY_LABELS, ACTION_LABELS, PROGRESS_STATUS_LABELS, PROGRESS_STATUS_COLORS } from './constants.js';
import state, { renderGuard } from './state.js';

export function getModelColor(id) {
  // Check agent config color first
  const agent = state.agents.find(a => a.model_id === id);
  if (agent?.color) return agent.color;
  const k = Object.keys(MODEL_COLORS).find(k => id.toLowerCase().includes(k));
  return k ? MODEL_COLORS[k] : '#8B949E';
}

export function esc(s) { if(!s) return ''; const d=document.createElement('div'); d.textContent=s; return d.innerHTML; }

export function _escapeAttr(s) {
  return String(s || '')
    .replace(/&/g, '&amp;')
    .replace(/"/g, '&quot;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

export function renderMd(s) {
  let t = esc(s || '');
  const stash = [];
  const save = (html) => {
    const token = `@@MD_${stash.length}@@`;
    stash.push(html);
    return token;
  };

  t = t.replace(/```[A-Za-z0-9_-]*\n([\s\S]*?)```/g, (_m, code) => save(`<pre><code>${code}</code></pre>`));
  t = t.replace(/```([\s\S]*?)```/g, (_m, code) => save(`<pre><code>${code}</code></pre>`));
  t = t.replace(/`([^`\n]+)`/g, (_m, code) => save(`<code>${code}</code>`));

  t = t.replace(/^(#{1,4})\s+(.+)$/gm, (_m, h, text) => {
    const sz = {1:'20px',2:'18px',3:'16px',4:'14px'}[h.length] || '14px';
    return save(`<div style="font-size:${sz};font-weight:700;margin:12px 0 6px;color:var(--text)">${text}</div>`);
  });

  t = t.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  t = t.replace(/~~([^~]+)~~/g, '<del>$1</del>');
  t = t.replace(/(^|[\s(])\*([^*\n]+)\*(?=$|[\s),.!?:;])/g, '$1<em>$2</em>');
  t = t.replace(/^(\s*)[-*] (.+)$/gm, '$1<span style="display:list-item;margin-left:20px">$2</span>');
  t = t.replace(/(^|[\s(])@([A-Za-z0-9_-]{4,64})(?![A-Za-z0-9_-])/g, (m, prefix, ref) => {
    const issue = findIssueByRef(ref);
    if (issue) {
      const encodedRef = encodeURIComponent(ref);
      const issueTitle = shortText(issue.title || issue.id, 36);
      return `${prefix}<button type="button" class="mention-link mention-issue" onclick="jumpToIssueFromMention(decodeURIComponent('${encodedRef}'))" title="${esc(issue.title || issue.id)}로 이동"><span class="mention-main">@${ref}</span><span class="mention-meta">${esc(issueTitle)}</span></button>`;
    }
    const agent = findAgentByRef(ref);
    if (agent && agent.model_id) {
      const encodedAgent = encodeURIComponent(agent.model_id);
      const desc = shortText(agent.description || '에이전트', 20);
      return `${prefix}<button type="button" class="mention-link mention-agent" onclick="jumpToAgentFromMention(decodeURIComponent('${encodedAgent}'))" title="@${esc(agent.model_id)} 창 열기"><span class="mention-main">@${esc(agent.model_id)}</span><span class="mention-meta">${esc(desc)}</span></button>`;
    }
    return m;
  });

  t = t.replace(/\n/g, '<br>');
  t = t.replace(/@@MD_(\d+)@@/g, (_m, idx) => stash[Number(idx)] || '');
  return t;
}

export function formatTs(ts) {
  if (!ts) return '';
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return '';
  return d.toLocaleString('ko-KR', { hour12: false });
}

export function sevLabel(s) { return SEVERITY_LABELS[s]||s; }

export function actLabel(a) { const key = (a||'').replace(/-/g,'_'); const label = ACTION_LABELS[key]; return label ? `${key} (${label})` : a; }

export function shortText(s, max = 32) {
  const text = (s || '').trim();
  if (!text) return '';
  return text.length > max ? `${text.slice(0, max - 1)}…` : text;
}

export function findIssueByRef(ref) {
  const key = (ref || '').trim();
  if (!key) return null;
  const exact = state.issues.find(i => i.id === key);
  if (exact) return exact;
  const matches = state.issues.filter(i => i.id.startsWith(key));
  if (matches.length === 1) return matches[0];
  return null;
}

export function findAgentByRef(ref) {
  const key = (ref || '').trim().toLowerCase();
  if (!key) return null;
  const exact = state.agents.find(a => (a.model_id || '').toLowerCase() === key);
  if (exact) return exact;
  const matches = state.agents.filter(a => (a.model_id || '').toLowerCase().startsWith(key));
  if (matches.length === 1) return matches[0];
  return null;
}

export function _normalizeReviewerAction(action) {
  let key = String(action || '').trim().toLowerCase();
  if (!key) return 'comment';
  if (key.includes('.')) key = key.split('.').pop() || key; // e.g. OpinionAction.FIX_REQUIRED
  key = key.replace(/\s+/g, '_');
  key = key.replace(/-+/g, '_');
  if (key === 'fixrequired') key = 'fix_required';
  if (key === 'nofix') key = 'no_fix';

  if (key.startsWith('status_change')) return 'status_change';
  if (key.startsWith('false_positive')) return 'false_positive';
  if (key.startsWith('withdraw')) return 'withdraw';
  if (key.startsWith('fix_required') || key.startsWith('agree')) return 'fix_required';
  if (key.startsWith('no_fix') || key.startsWith('disagree')) return 'no_fix';
  if (key.startsWith('comment') || key.startsWith('clarify')) return 'comment';
  if (key.startsWith('raise')) return 'raise';

  if (key.includes('오탐')) return 'false_positive';
  if (key.includes('철회')) return 'withdraw';
  if (key.includes('수정') && key.includes('불필요')) return 'no_fix';
  if (key.includes('수정') && key.includes('필요')) return 'fix_required';
  if (key.includes('의견')) return 'comment';
  if (key.includes('제기')) return 'raise';

  return 'comment';
}

export function _reviewerActionClass(action) {
  const key = _normalizeReviewerAction(action);
  if (key === 'raise') return 'reviewer-opinion-raise';
  if (key === 'fix_required') return 'reviewer-opinion-fix-required';
  if (key === 'no_fix') return 'reviewer-opinion-no-fix';
  if (key === 'false_positive') return 'reviewer-opinion-false-positive';
  if (key === 'withdraw') return 'reviewer-opinion-withdraw';
  if (key === 'status_change') return 'reviewer-opinion-status-change';
  if (key === 'comment') return 'reviewer-opinion-comment';
  return 'reviewer-opinion-comment';
}

export function _reviewerActionLabel(action) {
  const key = _normalizeReviewerAction(action);
  return ACTION_LABELS[key] || ACTION_LABELS.raise;
}

export function _isRaiseAction(action) {
  return _normalizeReviewerAction(action) === 'raise';
}

export function _isStatusChangeAction(action) {
  return _normalizeReviewerAction(action) === 'status_change';
}

export function progressBadgeHtml(status) {
  if (!status) return '';
  const label = PROGRESS_STATUS_LABELS[status] || status;
  const color = PROGRESS_STATUS_COLORS[status] || '#6B7280';
  return `<span class="progress-badge" style="background:${color}20;color:${color}">${esc(label)}</span>`;
}

export function voteTallyBadgeHtml(issue) {
  if (!issue) return '';
  const thread = Array.isArray(issue.thread) ? issue.thread : [];
  const seen = new Set();
  let fix = 0, comment = 0, nofix = 0;
  for (const op of thread) {
    if (!op) continue;
    const action = _normalizeReviewerAction(op.action || '');
    if (action === 'status_change') continue;
    if (seen.has(op.model_id)) continue;
    seen.add(op.model_id);
    if (action === 'raise' || action === 'fix_required') fix++;
    else if (action === 'comment') comment++;
    else if (action === 'no_fix' || action === 'false_positive' || action === 'withdraw') nofix++;
  }
  if (fix === 0 && comment === 0 && nofix === 0) return '';
  return `<div class="vote-tally-badge">수정 필요: <span class="vote-fix">${fix}</span> / 의견: <span class="vote-comment">${comment}</span> / 수정 불필요: <span class="vote-nofix">${nofix}</span></div>`;
}

export function _getInitialRaiseOpinion(issue) {
  const raisedBy = String(issue?.raised_by || '').trim();
  if (!raisedBy) return null;
  const thread = Array.isArray(issue?.thread) ? issue.thread : [];
  const candidates = thread.filter((op) => {
    if (!op) return false;
    if (String(op.model_id || '').trim() !== raisedBy) return false;
    if (!_isRaiseAction(op.action || '')) return false;
    return Number(op.turn || 0) === 0;
  });
  if (!candidates.length) return null;
  return candidates
    .slice()
    .sort((a, b) => new Date(a?.timestamp || 0).getTime() - new Date(b?.timestamp || 0).getTime())[0];
}

export function _getDiscussionOpinions(issue) {
  const thread = Array.isArray(issue?.thread) ? issue.thread : [];
  return thread.filter((op) => !_isRaiseAction(op?.action || ''));
}

export function _getLatestReviewerOpinion(issue, modelId) {
  const opinions = (issue?.thread || []).filter(op => op?.model_id === modelId);
  if (!opinions.length) return null;
  return opinions.slice().sort((a, b) => {
    const turnDiff = Number(b?.turn || 0) - Number(a?.turn || 0);
    if (turnDiff) return turnDiff;
    return new Date(b?.timestamp || 0).getTime() - new Date(a?.timestamp || 0).getTime();
  })[0];
}

export function normalizeTitle(title) {
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

export function _issueLineRange(issue) {
  const line = Number.isInteger(issue?.line) ? issue.line : null;
  let start = Number.isInteger(issue?.line_start) ? issue.line_start : line;
  let end = Number.isInteger(issue?.line_end) ? issue.line_end : start;
  if (start === null && end !== null) start = end;
  if (start !== null && end !== null && end < start) {
    const tmp = start;
    start = end;
    end = tmp;
  }
  return { start, end };
}

export function _issueRangeLabel(issue) {
  const r = _issueLineRange(issue);
  if (r.start === null) return '';
  if (r.end !== null && r.end !== r.start) return `${r.start}-${r.end}`;
  return String(r.start);
}

export function _isIssueTargetLine(issue, lineNo) {
  if (!Number.isInteger(lineNo)) return false;
  const r = _issueLineRange(issue);
  if (r.start === null) return false;
  const end = r.end ?? r.start;
  return lineNo >= r.start && lineNo <= end;
}

export function _hasActiveSelectionInIssueDetail() {
  const detail = document.getElementById('issue-detail');
  const sel = window.getSelection ? window.getSelection() : null;
  if (!detail || !sel || sel.rangeCount === 0 || sel.isCollapsed) return false;
  const anchor = sel.anchorNode;
  const focus = sel.focusNode;
  return !!((anchor && detail.contains(anchor)) || (focus && detail.contains(focus)));
}

export function _shouldDeferIssueRender() {
  return renderGuard._issueRenderPaused || _hasActiveSelectionInIssueDetail();
}

export function _flushDeferredIssueRender() {
  if (renderGuard._pollDeferredWhileSelecting) {
    renderGuard._pollDeferredWhileSelecting = false;
    window.schedulePoll(0);
  }
  if (!renderGuard._issueRenderPending) return;
  if (_shouldDeferIssueRender()) return;
  renderGuard._issueRenderPending = false;
  window.renderMainTabContent();
}

export function formatElapsed(sec) {
  if (sec == null) return '';
  if (sec < 60) return Math.floor(sec) + 's';
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return m + 'm ' + s + 's';
}

export function _escapeRegex(s) { return String(s || '').replace(/[.*+?^${}()|[\]\\]/g, '\\$&'); }

export function insertMention(mention) {
  const el = document.getElementById('comment-text');
  if (!el) return;
  const start = el.selectionStart || el.value.length;
  const end = el.selectionEnd || el.value.length;
  const before = el.value.slice(0, start);
  const after = el.value.slice(end);
  el.value = before + mention + after;
  const pos = start + mention.length;
  el.focus();
  el.setSelectionRange(pos, pos);
}
