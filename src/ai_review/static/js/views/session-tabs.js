import state, { uiState, shared } from '../state.js';
import { router } from '../router.js';
import { esc } from '../utils.js';
import { STATUS_TAB_STYLES, STATUS_TAB_LABELS } from '../constants.js';
import { closeSSE } from '../features/sse.js';
import { resetFileTreeAutoExpand } from './file-panel.js';
import { clearMiniDiffCache } from './conversation.js';

export { fetchSessions } from '../api.js';

export function renderSessionTabs() {
  const list = document.getElementById('session-tab-list');
  if (!list) return;
  if (!state.sessions.length) {
    list.innerHTML = '<span class="session-tabs-empty">세션 없음</span>';
    return;
  }
  list.innerHTML = state.sessions.map(s => {
    const shortId = s.session_id.slice(0, 6);
    const isActive = s.session_id === state.sessionId;
    const activeCls = isActive ? ' active' : '';
    const st = STATUS_TAB_STYLES[s.status] || STATUS_TAB_STYLES.idle;
    const stLabel = STATUS_TAB_LABELS[s.status] || s.status;
    return `<div class="session-tab${activeCls}" onclick="switchSession('${esc(s.session_id)}')">
      <span class="tab-id">${esc(shortId)}</span>
      <span class="tab-base">${esc(s.base || '')}</span>
      <span class="tab-status" style="background:${st.bg};color:${st.color}">${esc(stLabel)}</span>
      <button class="tab-close" onclick="event.stopPropagation();deleteSession('${esc(s.session_id)}')" title="세션 삭제">&times;</button>
    </div>`;
  }).join('');
}

export async function switchSession(sid, { push = true } = {}) {
  if (sid === state.sessionId) return;
  closeSSE();
  try {
    const r = await fetch(`/api/sessions/${sid}/activate`, { method: 'POST' });
    if (!r.ok) { await window.fetchSessions(); renderSessionTabs(); return; }
  } catch (e) { await window.fetchSessions(); renderSessionTabs(); return; }
  // Reset state
  state.sessionId = sid;
  state.status = 'idle';
  state.currentTurn = 0;
  state.issues = [];
  state.humanAssistAccessKey = shared._humanAssistKeyBySession[sid] || null;
  state.issueNumberById = {};
  state.nextIssueNumber = 1;
  state.selectedIssue = uiState._uiSelectedIssueBySession[sid] || null;
  state.selectedAgent = null;
  state.selectedFileDiff = null;
  state.diffCache = {};
  clearMiniDiffCache();
  state.implementationContext = null;
  state.reviews = [];
  state.files = [];
  state.fileTreeExpanded = {};
  resetFileTreeAutoExpand();
  state.agents = [];
  state.expandedDiffByIssue = {};
  state.collapsedIssueGroups = {};
  state.expandedReasoning = {};
  state.reviewerExpanded = {};
  window.switchMainTab('conversation', { push: false });
  window._uiSaveStateToStorage();
  if (push) router.push({ sessionId: sid, mainTab: 'conversation' });
  renderSessionTabs();
  window.connectSSE(sid);
  await window.pollStatus();
}

export async function deleteSession(sid) {
  if (!confirm('이 세션을 삭제하시겠습니까?')) return;
  try {
    const r = await fetch(`/api/sessions/${sid}`, { method: 'DELETE' });
    if (!r.ok && r.status !== 404) { alert('세션 삭제 실패'); return; }
  } catch (e) { alert('세션 삭제 실패'); return; }
  delete uiState._uiSelectedIssueBySession[sid];
  delete shared._humanAssistKeyBySession[sid];
  await window.fetchSessions();
  if (sid === state.sessionId) {
    if (state.sessions.length) {
      await switchSession(state.sessions[0].session_id);
    } else {
      closeSSE();
      state.sessionId = null;
      state.status = 'idle';
      state.currentTurn = 0;
      state.mainTab = 'conversation';
      state.issues = [];
      state.humanAssistAccessKey = null;
      state.files = [];
      state.fileTreeExpanded = {};
      resetFileTreeAutoExpand();
      state.agents = [];
      state.reviewerExpanded = {};
      renderSessionTabs();
      window.updateStepIndicator();
      window.updateSummary();
      window.renderMainTabContent();
      window.renderAgentPanel();
      window._uiSaveStateToStorage();
      router.replace({ sessionId: null });
    }
  } else {
    renderSessionTabs();
    window._uiSaveStateToStorage();
  }
}
