// SSE (Server-Sent Events) and Polling

import state, { uiState, renderGuard, shared } from '../state.js';

// Module-local state
let statusPollInterval = null;
let sseSource = null;
let pollInFlight = false;
let pollScheduled = false;

// Render guard utilities
function _hasActiveSelectionInIssueDetail() {
  const detail = document.getElementById('issue-detail');
  const sel = window.getSelection ? window.getSelection() : null;
  if (!detail || !sel || sel.rangeCount === 0 || sel.isCollapsed) return false;
  const anchor = sel.anchorNode;
  const focus = sel.focusNode;
  return !!((anchor && detail.contains(anchor)) || (focus && detail.contains(focus)));
}

function _shouldDeferIssueRender() {
  return renderGuard._issueRenderPaused || _hasActiveSelectionInIssueDetail();
}

export function schedulePoll(delay = 0) {
  if (pollScheduled) return;
  pollScheduled = true;
  setTimeout(() => {
    pollScheduled = false;
    pollStatus();
  }, delay);
}

export function startStatusPolling() {
  if (statusPollInterval) return;
  statusPollInterval = setInterval(() => schedulePoll(0), 5000);
}

export function stopStatusPolling() {
  if (!statusPollInterval) return;
  clearInterval(statusPollInterval);
  statusPollInterval = null;
}

export async function pollStatus() {
  if (!state.sessionId) return;
  if (_shouldDeferIssueRender()) {
    renderGuard._pollDeferredWhileSelecting = true;
    return;
  }
  if (pollInFlight) return;
  pollInFlight = true;
  try {
    const r = await fetch(`/api/sessions/${state.sessionId}/status`);
    if (!r.ok) return;
    const d = await r.json();
    state.status = d.status;
    state.currentTurn = Number.isFinite(Number(d.current_turn)) ? Number(d.current_turn) : 0;
    state.files = d.files || [];
    state.agents = d.agents || [];
    state.implementationContext = d.implementation_context || null;
    state.reviews = d.reviews || [];
    document.getElementById('stat-files').textContent = '파일 '+d.files_changed+'개';
    document.getElementById('stat-reviews').textContent = '리뷰 '+d.review_count+'개';
    document.getElementById('stat-issues').textContent = '이슈 '+d.issue_count+'개';
    const branchText = d.head ? `${d.head} \u2192 ${d.base}` : (d.base ? `\u2192 ${d.base}` : '--');
    document.getElementById('branch-info').textContent = branchText;
    window.updateStepIndicator();
    window.updateTabCounts();

    // Show/hide action buttons based on status
    const btnProcess = document.getElementById('btn-process');
    const btnFinish = document.getElementById('btn-finish');
    btnProcess.style.display = (d.status === 'reviewing' && d.review_count > 0) ? '' : 'none';
    btnFinish.style.display = (d.status === 'deliberating' || d.status === 'reviewing') ? '' : 'none';

    const ir = await fetch(`/api/sessions/${state.sessionId}/issues`);
    if (ir.ok) {
      const prev = state.selectedIssue ? JSON.stringify(state.issues.find(i => i.id === state.selectedIssue)) : null;
      state.issues = await ir.json();
      const restoredIssue = uiState._uiSelectedIssueBySession[state.sessionId];
      if ((!state.selectedIssue || !state.issues.some(i => i.id === state.selectedIssue)) && restoredIssue) {
        if (state.issues.some(i => i.id === restoredIssue)) state.selectedIssue = restoredIssue;
      }
      if (state.selectedIssue && !state.issues.some(i => i.id === state.selectedIssue)) {
        state.selectedIssue = null;
      }
      if (_shouldDeferIssueRender()) {
        renderGuard._issueRenderPending = true;
      } else {
        window.renderMainTabContent();
      }
    } else {
      if (_shouldDeferIssueRender()) renderGuard._issueRenderPending = true;
      else {
        window.renderMainTabContent();
      }
    }
    // Restore agent activities from server on refresh (when client has no data)
    if (d.agent_activities && window._onAgentActivity) {
      for (const [modelId, acts] of Object.entries(d.agent_activities)) {
        if (acts.length) {
          const reversed = [...acts].reverse();
          for (const act of reversed) {
            window._onAgentActivity({ model_id: modelId, ...act });
          }
        }
      }
    }
    window.renderAgentPanel();
    if (state.selectedAgent) window.refreshAgentChat();
    window.updateSummary();
    await window.fetchSessions();
    window.renderSessionTabs();
  } catch(e) {
  } finally {
    pollInFlight = false;
  }
}

export function closeSSE() {
  if (sseSource) { sseSource.close(); sseSource = null; }
}

export function connectSSE(sid) {
  closeSSE();
  const src = new EventSource(`/api/sessions/${sid}/stream`);
  sseSource = src;
  src.onopen = () => {
    stopStatusPolling();
    schedulePoll(0);
  };
  src.addEventListener('phase_change', ()=>schedulePoll(50));
  src.addEventListener('review_submitted', ()=>schedulePoll(50));
  src.addEventListener('opinion_submitted', ()=>schedulePoll(50));
  src.addEventListener('agent_status', ()=>schedulePoll(50));
  src.addEventListener('issue_created', ()=>schedulePoll(50));
  src.addEventListener('issue_status_changed', ()=>schedulePoll(50));
  src.addEventListener('agent_config_changed', ()=>schedulePoll(50));
  src.addEventListener('agent_activity', (ev) => {
    try {
      const data = JSON.parse(ev.data);
      if (window._onAgentActivity) window._onAgentActivity(data);
    } catch(e) {}
  });
  src.onerror = ()=>{
    src.close();
    if (sseSource === src) sseSource = null;
    startStatusPolling();
    setTimeout(()=>{ if(state.sessionId) connectSSE(state.sessionId); }, 3000);
  };
}
