import state, { renderGuard, shared, _uiSaveStateToStorage } from '../state.js';
import { esc, _escapeAttr, getModelColor, renderMd, _getDiscussionOpinions, _issueRangeLabel, _shouldDeferIssueRender, _getInitialRaiseOpinion, _isStatusChangeAction, progressBadgeHtml } from '../utils.js';
import { SEVERITY_COLORS, SEVERITY_LABELS, ACTION_LABELS, PROGRESS_STATUS_LABELS } from '../constants.js';
import { fetchDiff } from '../api.js';
import { renderDiffWithFocus } from '../diff/renderer.js';
import { _renderDiffStatsMeta } from '../diff/renderer.js';

const { _issueDetailRenderSeq } = renderGuard;

export function renderIssueDetailEmpty() {
  return '<div class="empty-state"><div class="icon">&#128221;</div><div class="message">이슈를 선택하세요</div><div class="hint">이슈를 클릭하면 코드와 토론을 볼 수 있습니다</div></div>';
}

export function renderAgentSessionDetail() {
  if (!state.agents.length) {
    return '<div class="empty-state"><div class="icon">&#129302;</div><div class="message">에이전트가 없습니다</div><div class="hint">프리셋을 선택해 세션을 시작하세요</div></div>';
  }

  const statusLabel = (status) => {
    if (status === 'reviewing') return '진행중';
    if (status === 'submitted') return '완료';
    if (status === 'failed') return '실패';
    return '대기';
  };

  return `<div class="agent-session-board">${
    state.agents.map((agent) => {
      const modelColor = getModelColor(agent.model_id);
      return `<div class="agent-session-card">
        <div class="agent-session-head">
          <span class="model-dot" style="background:${modelColor}"></span>
          <span class="agent-session-name" style="color:${modelColor}">${esc(agent.model_id)}</span>
          <span class="agent-session-status">${esc(statusLabel(agent.status))}</span>
          <button class="btn" style="margin-left:auto;padding:4px 8px;font-size:11px" onclick="openAgentChat('${esc(agent.model_id)}')">대화</button>
        </div>
        <div class="agent-session-empty">에이전트 활동을 추적 중입니다.</div>
      </div>`;
    }).join('')
  }</div>`;
}

function sevLabel(s) { return SEVERITY_LABELS[s]||s; }
function actLabel(a) { const key = (a||'').replace(/-/g,'_'); const label = ACTION_LABELS[key]; return label ? `${key} (${label})` : a; }
function formatTs(ts) {
  if (!ts) return '';
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return '';
  return d.toLocaleString('ko-KR', { hour12: false });
}

export function renderReasoning(text, key, maxChars=260) {
  const raw = text || '';
  const expanded = !!state.expandedReasoning[key];
  if (raw.length <= maxChars) return `<div class="opinion-text">${renderMd(raw)}</div>`;
  const shown = expanded ? raw : `${raw.slice(0, maxChars)}...`;
  return `<div class="opinion-text">${renderMd(shown)}</div>
    <button class="opinion-more" onclick="toggleReasoning('${esc(key)}')">${expanded ? '접기' : '더보기'}</button>`;
}

export async function renderIssueDetail() {
  const renderSeq = ++renderGuard._issueDetailRenderSeq;
  const el = document.getElementById('issue-detail');
  if (!el) return;
  if (!state.selectedIssue) { el.innerHTML = renderIssueDetailEmpty(); return; }
  const issue = state.issues.find(i=>i.id===state.selectedIssue);
  if (!issue) return;

  const sev = issue.final_severity||issue.severity;
  const sevColor = SEVERITY_COLORS[sev]||'#6B7280';
  const rangeLabel = _issueRangeLabel(issue);
  const fileLine = rangeLabel ? `${issue.file}:${rangeLabel}` : `${issue.file} (라인 미지정)`;

  // Header
  let html = `<div class="detail-header-bar">
    <div class="detail-title">${esc(issue.title)}</div>
    <div class="detail-file">${esc(fileLine)}</div>
    <div class="detail-badges">
      <span class="severity-badge" style="background:${sevColor}20;color:${sevColor}">${sevLabel(sev)}</span>
      ${progressBadgeHtml(issue.progress_status)}
      <span style="font-size:12px;color:var(--text-muted)">제기: <span style="color:${getModelColor(issue.raised_by||'')}">${esc(issue.raised_by)}</span></span>
    </div>
  </div>`;

  // Initial raise (not a comment): always render as standalone issue block.
  const initialRaise = _getInitialRaiseOpinion(issue);
  const raisedAt = initialRaise?.timestamp ? formatTs(initialRaise.timestamp) : '';
  html += `<div class="issue-origin-panel">
    <div class="issue-origin-head">
      <span class="issue-origin-title">최초 이슈 제기</span>
      <span class="model-dot" style="background:${getModelColor(issue.raised_by || '')}"></span>
      <span class="model-name" style="color:${getModelColor(issue.raised_by || '')}">${esc(issue.raised_by || 'unknown')}</span>
      <span class="action-badge action-raise">raise (제기)</span>
      ${raisedAt ? `<span class="issue-origin-meta">${esc(raisedAt)}</span>` : ''}
    </div>
    <div class="issue-origin-body">
      <div class="opinion-text">${issue.description ? renderMd(issue.description) : '<span style="color:var(--text-muted)">설명이 없습니다.</span>'}</div>
      ${issue.suggestion ? `<div class="issue-origin-suggestion"><div class="opinion-suggestion">${renderMd(issue.suggestion)}</div></div>` : ''}
    </div>
  </div>`;

  // Diff
  const isFullDiff = !!state.expandedDiffByIssue[issue.id];
  const issueStatsMeta = _renderDiffStatsMeta(issue.file);
  html += `<div class="diff-file-header"><span class="filename">${esc(issue.file)}</span>${issueStatsMeta}<button class="btn" onclick="toggleIssueDiff('${issue.id}')">${isFullDiff ? '축약 보기' : '전체 diff 보기'}</button></div>`;
  html += '<div class="diff-container">';

  const diffContent = await fetchDiff(issue.file);
  if (renderSeq !== renderGuard._issueDetailRenderSeq) return;
  html += renderDiffWithFocus(diffContent, issue, 20, isFullDiff);
  html += '</div>';

  // Discussion thread (exclude initial raise opinion)
  const discussionThread = _getDiscussionOpinions(issue);
  if (discussionThread.length) {
    const mode = state.issueDetailModeByIssue[issue.id] || 'timeline';
    const isTimeline = mode === 'timeline';
    const timeline = [...discussionThread].sort((a, b) => new Date(b.timestamp || 0) - new Date(a.timestamp || 0));
    html += `<div style="padding:10px 20px;border-bottom:1px solid var(--border);display:flex;gap:8px">
      <button class="btn ${isTimeline ? 'btn-primary' : ''}" onclick="setIssueDetailMode('${issue.id}','timeline')">타임라인</button>
      <button class="btn ${!isTimeline ? 'btn-primary' : ''}" onclick="setIssueDetailMode('${issue.id}','thread')">스레드</button>
    </div>`;
    if (isTimeline) {
      html += `<div class="thread-panel"><div class="thread-title">타임라인 (${timeline.length})</div>`;
      timeline.forEach((op, idx) => {
        if (_isStatusChangeAction(op.action)) {
          const mColor = getModelColor(op.model_id);
          const _isAuthor = op.model_id !== issue.raised_by;
          const _roleBadge = _isAuthor
            ? '<span class="action-badge" style="background:rgba(245,158,11,0.12);color:#F59E0B">Author</span>'
            : '<span class="action-badge" style="background:rgba(99,102,241,0.12);color:#818CF8">Reviewer</span>';
          html += `<div class="status-change-log">
            <span class="status-change-arrow">&rarr;</span>
            <span class="model-dot" style="background:${mColor};width:8px;height:8px"></span>
            <span class="status-change-author" style="color:${mColor}">${esc(op.model_id || '')}</span>
            ${_roleBadge}
            ${op.status_value
              ? (op.previous_status
                ? `가 상태를 ${progressBadgeHtml(op.previous_status)} 에서 ${progressBadgeHtml(op.status_value)} 로 변경했습니다.`
                : `가 상태를 ${progressBadgeHtml(op.status_value)} (으)로 변경했습니다.`)
              : esc(op.reasoning || '')}
            <span class="status-change-time">${op.timestamp ? esc(formatTs(op.timestamp)) : ''}</span>
          </div>`;
          return;
        }
        const mColor = getModelColor(op.model_id);
        const actionClass = 'action-'+op.action;
        const isReporter = op.model_id === issue.raised_by;
        const rk = `tl-${issue.id}-${idx}`;
        html += `<div class="timeline-item" data-op-model="${_escapeAttr(op.model_id || '')}" data-op-time="${_escapeAttr(op.timestamp || '')}" data-op-action="${_escapeAttr(op.action || '')}">
          <div class="timeline-head">
            <span class="model-dot" style="background:${mColor}"></span>
            <span class="model-name" style="color:${mColor}">${esc(op.model_id)}</span>
            <span class="action-badge ${actionClass}">${actLabel(op.action)}</span>
            ${isReporter ? '<span class="action-badge" style="background:rgba(245,158,11,0.12);color:#F59E0B">Reporter</span>' : ''}
            ${op.turn != null ? `<span class="severity-badge" style="background:#6B728020;color:#8B949E">턴 ${op.turn}</span>` : ''}
            <span class="timeline-time">${op.timestamp ? esc(formatTs(op.timestamp)) : ''}</span>
          </div>
          ${renderReasoning(op.reasoning, rk)}
        </div>`;
      });
      html += `</div>`;
    } else {
    html += `<div class="section-card">
      <div class="section-header" onclick="toggleSection('issue-thread-${issue.id}', 'issue-thread-toggle-${issue.id}')">
        <span class="section-toggle collapsed" id="issue-thread-toggle-${issue.id}">▾</span>
        <span>토론 (${discussionThread.length})</span>
      </div>
      <div class="section-body collapsed" id="issue-thread-${issue.id}">
      <div class="thread-panel">`;
    discussionThread.forEach((op, idx) => {
      if (_isStatusChangeAction(op.action)) {
        const mColor = getModelColor(op.model_id);
        const _isAuthor = op.model_id !== issue.raised_by;
        const _roleBadge = _isAuthor
          ? '<span class="action-badge" style="background:rgba(245,158,11,0.12);color:#F59E0B">Author</span>'
          : '<span class="action-badge" style="background:rgba(99,102,241,0.12);color:#818CF8">Reviewer</span>';
        html += `<div class="status-change-log">
          <span class="status-change-arrow">&rarr;</span>
          <span class="model-dot" style="background:${mColor};width:8px;height:8px"></span>
          <span class="status-change-author" style="color:${mColor}">${esc(op.model_id || '')}</span>
          ${_roleBadge}
          ${op.status_value
            ? (op.previous_status
              ? `가 상태를 ${progressBadgeHtml(op.previous_status)} 에서 ${progressBadgeHtml(op.status_value)} 로 변경했습니다.`
              : `가 상태를 ${progressBadgeHtml(op.status_value)} (으)로 변경했습니다.`)
            : esc(op.reasoning || '')}
          <span class="status-change-time">${op.timestamp ? esc(formatTs(op.timestamp)) : ''}</span>
        </div>`;
        return;
      }
      const mColor = getModelColor(op.model_id);
      const actionClass = 'action-'+op.action;
      const isReporter = op.model_id === issue.raised_by;
      const rk = `th-${issue.id}-${idx}`;
      html += `<div class="opinion" ${op.id ? `id="op-${_escapeAttr(op.id)}"` : ''} data-op-model="${_escapeAttr(op.model_id || '')}" data-op-time="${_escapeAttr(op.timestamp || '')}" data-op-action="${_escapeAttr(op.action || '')}">
        <div class="opinion-header">
          <span class="model-dot" style="background:${mColor}"></span>
          <span class="model-name" style="color:${mColor}">${esc(op.model_id)}</span>
          <span class="action-badge ${actionClass}">${actLabel(op.action)}</span>
          ${isReporter ? '<span class="action-badge" style="background:rgba(245,158,11,0.12);color:#F59E0B">Reporter</span>' : ''}
          ${op.suggested_severity?`<span class="severity-badge" style="background:${SEVERITY_COLORS[op.suggested_severity]||'#6B7280'}20;color:${SEVERITY_COLORS[op.suggested_severity]||'#6B7280'}">${sevLabel(op.suggested_severity)}</span>`:''}
          ${op.timestamp ? `<span class="opinion-time">${esc(formatTs(op.timestamp))}</span>` : ''}
        </div>
        ${renderReasoning(op.reasoning, rk)}
      </div>`;
    });
    html += '</div></div></div>';
    }
  }

  // Consensus
  if (issue.consensus===true && issue.consensus_type==='closed') {
    html += `<div style="padding:0 20px 16px"><div class="consensus-box consensus-reached">\uD83D\uDEAB False Positive (종결)</div></div>`;
  } else if (issue.consensus===true) {
    html += `<div style="padding:0 20px 16px"><div class="consensus-box consensus-reached">\u2705 합의 완료 \u2192 ${sevLabel(sev)}</div></div>`;
  } else if (discussionThread.length > 0) {
    html += `<div style="padding:0 20px 16px"><div class="consensus-box consensus-pending">\u23F3 합의 대기 중</div></div>`;
  }

  // Comment form
  html += `<div class="comment-form">
      <textarea id="comment-text" placeholder="이 이슈에 대한 의견을 작성하세요..."></textarea>
      <div class="field-hint">Markdown 지원: **굵게**, *기울임*, ~~취소선~~, \`코드\`, @이슈ID(클릭 이동)</div>
      <div class="comment-actions">
        <button class="btn btn-agree" onclick="submitOpinion('${issue.id}','fix_required')">수정필요</button>
        <button class="btn btn-disagree" onclick="submitOpinion('${issue.id}','no_fix')">수정불필요</button>
        <button class="btn" style="border-color:#A855F7;color:#A855F7" onclick="submitOpinion('${issue.id}','false_positive')">오탐</button>
        <button class="btn btn-comment" onclick="submitOpinion('${issue.id}','comment')">의견</button>
        <div class="spacer"></div>
        <select id="comment-severity" style="background:var(--bg);color:var(--text);border:1px solid var(--border);border-radius:4px;padding:4px 8px;font-size:12px;">
          <option value="">-- 심각도 --</option>
          <option value="critical">심각</option>
          <option value="high">높음</option>
          <option value="medium">보통</option>
          <option value="low">낮음</option>
          <option value="dismissed">기각</option>
        </select>
      </div>
      <div class="mention-row">
        ${state.agents.map(a => `<button class="mention-chip" onclick="insertMention('@${esc(a.model_id)} ')">@${esc(a.model_id)}</button>`).join('')}
      </div>
    </div>`;

  // Assist (issue resolution helper)
  const hasHistory = issue.assist_messages && issue.assist_messages.length > 0;
  const openClass = hasHistory ? ' open' : '';
  html += `<div class="assist-section">
    <div class="assist-toggle" onclick="toggleAssist('${issue.id}')">
      <span style="font-size:16px">&#129302;</span>
      <span class="label">해결 도우미</span>
      <span class="hint">${hasHistory ? issue.assist_messages.length + '개 메시지' : 'AI에게 이슈 해결을 요청하세요'}</span>
    </div>
    <div class="assist-chat${openClass}" id="assist-chat-${issue.id}">
      <div class="assist-messages" id="assist-messages-${issue.id}">
        ${hasHistory ? window.renderAssistMessages(issue.assist_messages) : '<div style="padding:8px;color:var(--text-muted);font-size:13px;text-align:center">아래에서 질문하거나 "이 이슈를 설명해줘"를 눌러보세요</div>'}
      </div>
      <div class="assist-cli">
        <div class="assist-cli-box" onclick="copyCliCommand('${issue.id}')" title="클릭하면 복사됩니다">
          <span style="color:var(--accent)">$</span>
          <span class="cmd" id="assist-cli-${issue.id}">claude -p "${esc(issue.file)} 파일의 이슈를 해결해주세요: ${esc(issue.title)}"</span>
          <span class="copy-hint">복사</span>
        </div>
      </div>
      <div class="assist-input-bar">
        <button class="btn btn-primary" style="font-size:12px;padding:8px 12px" onclick="sendAssist('${issue.id}','이 이슈에 대해 설명해주고, 어떻게 해결하면 좋을지 제안해줘')">설명 요청</button>
        <button class="btn" style="font-size:12px;padding:8px 12px" onclick="issueHumanAssistKey()">도우미 키 발급</button>
        <button class="btn" style="font-size:12px;padding:8px 12px" onclick="submitAssistOpinion('${issue.id}')">AI 의견 제출</button>
        <textarea class="assist-input" id="assist-input-${issue.id}" placeholder="질문을 입력하세요..." rows="1" onkeydown="if(event.key==='Enter'&&!event.shiftKey){event.preventDefault();sendAssistFromInput('${issue.id}')}"></textarea>
        <button class="btn-assist-send" id="assist-send-${issue.id}" onclick="sendAssistFromInput('${issue.id}')">전송</button>
      </div>
    </div>
  </div>`;

  if (renderSeq !== renderGuard._issueDetailRenderSeq) return;
  if (_shouldDeferIssueRender()) {
    renderGuard._issueRenderPending = true;
    return;
  }
  el.innerHTML = html;
  window._applyPendingOpinionJump(issue.id);
}

export async function renderFileDiff(path) {
  const el = document.getElementById('issue-detail');
  const diffContent = await fetchDiff(path);
  const statsMeta = _renderDiffStatsMeta(path);
  el.innerHTML = `
    <div class="detail-header-bar">
      <div class="detail-title" style="font-family:'SF Mono',Monaco,monospace">${esc(path)}</div>
      <div class="detail-badges" style="margin-top:8px">
        <button class="btn" onclick="createIssueFromFile('${esc(path)}')">+ 이슈 등록</button>
      </div>
    </div>
    <div class="diff-file-header"><span class="filename">${esc(path)}</span>${statsMeta}</div>
    <div class="diff-container">
      ${diffContent ? window.renderDiff(diffContent, { file: path }) : '<div class="diff-loading">변경 내역 없음</div>'}
    </div>`;
}

export function toggleReasoning(key) {
  state.expandedReasoning[key] = !state.expandedReasoning[key];
  renderIssueDetail();
}

export function toggleIssueDiff(issueId) {
  state.expandedDiffByIssue[issueId] = !state.expandedDiffByIssue[issueId];
  renderIssueDetail();
}

export function setIssueDetailMode(issueId, mode) {
  state.issueDetailModeByIssue[issueId] = mode;
  _uiSaveStateToStorage();
  renderIssueDetail();
}
