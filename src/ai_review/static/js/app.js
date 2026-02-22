// Application Entry Point

// Import all modules
import state, { uiState, renderGuard, shared, _uiRestoreStateFromStorage, _uiSaveStateToStorage } from './state.js';
import { router, _scrollToOpinion, initRouter } from './router.js';
import * as constants from './constants.js';
import * as utils from './utils.js';
import * as diffParser from './diff/parser.js';
import * as diffRenderer from './diff/renderer.js';
import * as diffHighlighter from './diff/highlighter.js';

// Import views
import * as conversationView from './views/conversation.js';
import * as changesView from './views/changes.js';
import * as issueDetailView from './views/issue-detail.js';
import * as issueListView from './views/issue-list.js';
import * as reviewersView from './views/reviewers.js';
import * as agentPanelView from './views/agent-panel.js';
import * as filePanelView from './views/file-panel.js';
import * as sessionTabsView from './views/session-tabs.js';

// Import features
import * as assist from './features/assist.js';
import * as agentChat from './features/agent-chat.js';
import * as opinions from './features/opinions.js';
import * as sse from './features/sse.js';

// Import modals
import * as agentManager from './modals/agent-manager.js';
import * as newSession from './modals/new-session.js';
import * as report from './modals/report.js';

// Import resize handlers
import { initResize, initReviewersResize, initIssueRenderGuard, initChangesSidebarResize, _syncLeftPaneLayout } from './resize.js';

// --- Window bindings for onclick handlers ---
Object.assign(window, {
  // From views
  switchMainTab,
  renderMainTabContent,
  selectIssue: issueListView.selectIssue,
  scrollToFileInChanges: conversationView.scrollToFileInChanges,
  toggleConvIssue: conversationView.toggleConvIssue,
  scrollToChangesFile: changesView.scrollToChangesFile,
  renderChangesSidebar: changesView.renderChangesSidebar,
  onChangesFileFilterInput: changesView.onChangesFileFilterInput,
  toggleReasoning: issueDetailView.toggleReasoning,
  toggleSection: changesView.toggleSection,
  toggleIssueDiff: issueDetailView.toggleIssueDiff,
  setIssueDetailMode: issueDetailView.setIssueDetailMode,
  toggleIssueGroup: issueListView.toggleIssueGroup,

  // From file panel
  toggleFilePanel: filePanelView.toggleFilePanel,
  onFileFilterQueryInput: filePanelView.onFileFilterQueryInput,
  toggleFileFilterPopover: filePanelView.toggleFileFilterPopover,
  onFileTreeFileClick: filePanelView.onFileTreeFileClick,
  onFileTreeDirClick: filePanelView.onFileTreeDirClick,
  onFileTreeFileContextMenu: filePanelView.onFileTreeFileContextMenu,
  onFileTreeDirContextMenu: filePanelView.onFileTreeDirContextMenu,
  onFileTreeRowEnter: filePanelView.onFileTreeRowEnter,
  onFileTreeRowMove: filePanelView.onFileTreeRowMove,
  onFileTreeRowLeave: filePanelView.onFileTreeRowLeave,
  filterByFile: filePanelView.filterByFile,
  startLeftPaneSplitDrag: filePanelView.startLeftPaneSplitDrag,

  // From reviewers
  toggleReviewerIssues: reviewersView.toggleReviewerIssues,
  openReviewerChat: reviewersView.openReviewerChat,
  jumpToIssueFromReviewer: reviewersView.jumpToIssueFromReviewer,
  jumpToIssueFromMention: reviewersView.jumpToIssueFromMention,
  jumpToAgentFromMention: reviewersView.jumpToAgentFromMention,

  // From agent panel
  renderAgentPanel: agentPanelView.renderAgentPanel,
  _onAgentActivity: agentPanelView._onAgentActivity,

  // From modals
  openAddAgentModal: agentManager.openAddAgentModal,
  openEditAgentModal: agentManager.openEditAgentModal,
  openNewSessionModal: newSession.openNewSessionModal,
  openAgentManager: agentManager.openAgentManager,
  _amRequestClose: agentManager._amRequestClose,
  _amShowAddPanel: agentManager._amShowAddPanel,
  _amSelectAgent: agentManager._amSelectAgent,
  _amApplyPreset: agentManager._amApplyPreset,
  _amUpdateStrictnessUI: agentManager._amUpdateStrictnessUI,
  _amOnClientTypeChange: agentManager._amOnClientTypeChange,
  _amSaveAgent: agentManager._amSaveAgent,
  _amDeleteAgent: agentManager._amDeleteAgent,
  _amSelectAddPreset: agentManager._amSelectAddPreset,
  _amApplyNewPreset: agentManager._amApplyNewPreset,
  _amUpdateNewStrictnessUI: agentManager._amUpdateNewStrictnessUI,
  _amOnNewClientTypeChange: agentManager._amOnNewClientTypeChange,
  _amTestConnection: agentManager._amTestConnection,
  _amShowConnTestDetail: agentManager._amShowConnTestDetail,
  _amSubmitNewAgent: agentManager._amSubmitNewAgent,
  removeAgent: agentManager.removeAgent,
  toggleAgentEnabled: agentManager.toggleAgentEnabled,
  getUniqueAgentId: agentManager.getUniqueAgentId,

  _nsmOpenPresetAdd: newSession._nsmOpenPresetAdd,
  _nsmOpenPresetManager: newSession._nsmOpenPresetManager,
  _nsmRefreshPresets: newSession._nsmRefreshPresets,
  _nsmTogglePreset: newSession._nsmTogglePreset,
  _openBranchPicker: newSession._openBranchPicker,
  _nsmValidateRepo: newSession._nsmValidateRepo,
  _nsmPickRepoPath: newSession._nsmPickRepoPath,
  submitNewSession: newSession.submitNewSession,

  // From features
  toggleAssist: assist.toggleAssist,
  sendAssist: assist.sendAssist,
  sendAssistFromInput: assist.sendAssistFromInput,
  copyCliCommand: assist.copyCliCommand,
  issueHumanAssistKey: assist.issueHumanAssistKey,
  submitAssistOpinion: assist.submitAssistOpinion,

  openAgentChat: agentChat.openAgentChat,
  closeAgentChat: agentChat.closeAgentChat,
  sendAgentChat: agentChat.sendAgentChat,
  refreshAgentChat: agentChat.refreshAgentChat,

  submitOpinion: opinions.submitOpinion,
  createIssueFromFile: opinions.createIssueFromFile,
  submitNewIssue: opinions.submitNewIssue,
  processReviews: opinions.processReviews,
  finishReview: opinions.finishReview,

  insertMention,

  // From session tabs
  switchSession: sessionTabsView.switchSession,
  deleteSession: sessionTabsView.deleteSession,
  fetchSessions: sessionTabsView.fetchSessions,
  renderSessionTabs: sessionTabsView.renderSessionTabs,

  // From report
  showReport: report.showReport,
  showToast: report.showToast,

  // From sse
  schedulePoll: sse.schedulePoll,
  pollStatus: sse.pollStatus,
  closeSSE: sse.closeSSE,
  connectSSE: sse.connectSSE,
  startStatusPolling: sse.startStatusPolling,
  stopStatusPolling: sse.stopStatusPolling,

  // From router
  router,

  // Theme
  toggleTheme,

  // Orchestration functions from this file
  updateTabCounts,
  updateStepIndicator,
  updateSummary,

  // From resize
  _syncLeftPaneLayout,

  // Issue context menu
  showIssueMenu,

  // Cross-module calls via window
  renderAssistMessages: assist.renderAssistMessages,
  _applyPendingOpinionJump: reviewersView._applyPendingOpinionJump,
  renderDiff: diffRenderer.renderDiff,
  renderIssueList: issueListView.renderIssueList,
  renderFileDiff: issueDetailView.renderFileDiff,
  _uiSaveStateToStorage,
  findAgentByRef: utils.findAgentByRef,

  // Expose constants for agent-panel
  MAX_ACTIVITY_HISTORY: constants.MAX_ACTIVITY_HISTORY,
});

// --- Orchestration functions ---

export function switchMainTab(tab, { force = false, push = true } = {}) {
  const changed = state.mainTab !== tab;
  state.mainTab = tab;
  document.querySelectorAll('.main-tab').forEach(b => {
    b.classList.toggle('active', b.dataset.tab === tab);
  });
  const sidebar = document.getElementById('changes-sidebar');
  const splitter = document.getElementById('changes-sidebar-splitter');
  const showSidebar = state.files.length > 0;
  if (sidebar) sidebar.style.display = showSidebar ? '' : 'none';
  if (splitter) splitter.style.display = showSidebar ? '' : 'none';
  if (changed || force) renderMainTabContent();
  if (changed && push) router.push({ sessionId: state.sessionId, mainTab: tab });
}

export function renderMainTabContent() {
  const container = document.getElementById('main-tab-content');
  if (!container) return;
  if (state.mainTab === 'conversation') {
    conversationView.renderConversationTab(container);
  } else if (state.mainTab === 'changes') {
    changesView.renderChangesTab(container);
  }
  // Sync sidebar visibility (initial load may skip switchMainTab)
  const sidebar = document.getElementById('changes-sidebar');
  const splitter = document.getElementById('changes-sidebar-splitter');
  const showSidebar = state.files.length > 0;
  if (sidebar) sidebar.style.display = showSidebar ? '' : 'none';
  if (splitter) splitter.style.display = showSidebar ? '' : 'none';
  if (showSidebar) changesView.renderChangesSidebar();
}

export function updateTabCounts() {
  const convBadge = document.getElementById('tab-badge-conversation');
  const changesBadge = document.getElementById('tab-badge-changes');
  if (convBadge) convBadge.textContent = state.reviews.length ? state.reviews.length : '';
  if (changesBadge) changesBadge.textContent = state.files.length ? state.files.length : '';
}

export function updateStepIndicator() {
  // Step indicator removed from UI
}

export function updateSummary() {
  const el = document.getElementById('summary-bar');
  const t = state.issues.length;
  const c = state.issues.filter(i=>i.consensus===true).length;
  const d = state.issues.filter(i=>(i.final_severity||i.severity)==='dismissed').length;
  const stats = agentPanelView._getActivityStats();
  if (!t && !stats) { el.textContent='준비 완료'; return; }
  const issuePart = t ? `이슈 ${t}개 · 합의 ${c}개 · 기각 ${d}개` : '준비 완료';
  el.innerHTML = stats ? `${utils.esc(issuePart)} ${stats}` : utils.esc(issuePart);
}

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

// --- Issue Context Menu ---

function _hideIssueContextMenu() {
  const menu = document.getElementById('issue-context-menu');
  if (!menu) return;
  menu.classList.remove('show');
  menu.style.left = '-9999px';
  menu.style.top = '-9999px';
}

function _ensureIssueContextMenu() {
  let menu = document.getElementById('issue-context-menu');
  if (menu) return menu;
  menu = document.createElement('div');
  menu.id = 'issue-context-menu';
  menu.className = 'file-context-menu';
  menu.addEventListener('click', async (e) => {
    const btn = e.target?.closest?.('.file-context-item');
    if (!btn) return;
    if (btn.dataset?.action === 'copy-ref') {
      const ref = menu.dataset.issueRef || '';
      await navigator.clipboard.writeText(ref);
      report.showToast('이슈 번호가 복사되었습니다');
    }
    _hideIssueContextMenu();
  });
  document.body.appendChild(menu);
  document.addEventListener('click', (e) => { if (!menu.contains(e.target)) _hideIssueContextMenu(); });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') _hideIssueContextMenu(); });
  return menu;
}

function showIssueMenu(event, sessionId, displayNo) {
  event.preventDefault();
  event.stopPropagation();
  const menu = _ensureIssueContextMenu();
  const ref = `${sessionId}#${displayNo}`;
  menu.dataset.issueRef = ref;
  menu.innerHTML = `<button type="button" class="file-context-item" data-action="copy-ref">이슈 번호 복사 <span style="color:var(--text-muted);margin-left:4px">${utils.esc(ref)}</span></button>`;
  menu.style.left = '0px';
  menu.style.top = '0px';
  menu.classList.add('show');
  const rect = menu.getBoundingClientRect();
  const x = event.clientX || 0;
  const y = event.clientY || 0;
  const maxLeft = Math.max(8, window.innerWidth - rect.width - 8);
  const maxTop = Math.max(8, window.innerHeight - rect.height - 8);
  menu.style.left = `${Math.max(8, Math.min(x, maxLeft))}px`;
  menu.style.top = `${Math.max(8, Math.min(y, maxTop))}px`;
}

// --- Theme ---

function toggleTheme() {
  const isLight = document.documentElement.getAttribute('data-theme') === 'light';
  const next = isLight ? 'dark' : 'light';
  if (next === 'light') {
    document.documentElement.setAttribute('data-theme', 'light');
  } else {
    document.documentElement.removeAttribute('data-theme');
  }
  localStorage.setItem('ai-review-theme', next);
  _syncThemeIcon();
}

function _syncThemeIcon() {
  const btn = document.getElementById('theme-toggle');
  if (!btn) return;
  const isLight = document.documentElement.getAttribute('data-theme') === 'light';
  btn.textContent = isLight ? '\u263E' : '\u2600';
  btn.title = isLight ? '\uB2E4\uD06C \uBAA8\uB4DC\uB85C \uC804\uD658' : '\uB77C\uC774\uD2B8 \uBAA8\uB4DC\uB85C \uC804\uD658';
}

// --- Initialization ---

async function init() {
  _syncThemeIcon();
  const savedUI = _uiRestoreStateFromStorage();
  diffHighlighter._ensureDiffHighlighter();
  filePanelView._loadFileOpeners();
  await sessionTabsView.fetchSessions();
  sessionTabsView.renderSessionTabs();

  // Deep link: URL에서 세션/이슈 파싱
  const urlState = router.parse();
  const urlSessionValid = urlState.sessionId && state.sessions.some(s => s.session_id === urlState.sessionId);

  let currentSessionId = null;
  try {
    const r = await fetch('/api/sessions/current/status');
    if (r.ok) {
      const d = await r.json();
      currentSessionId = d.session_id;
      state.status = d.status;
      state.issueNumberById = {};
      state.nextIssueNumber = 1;
    }
  } catch(e) {}

  // URL 딥링크 우선 → localStorage → 서버 현재 세션
  let targetSessionId;
  if (urlSessionValid) {
    targetSessionId = urlState.sessionId;
  } else {
    const savedSessionId = savedUI?.session_id || '';
    const hasSavedSession = !!savedSessionId && state.sessions.some(s => s.session_id === savedSessionId);
    targetSessionId = hasSavedSession ? savedSessionId : currentSessionId;
  }

  if (targetSessionId && targetSessionId !== currentSessionId) {
    state.sessionId = currentSessionId || null;
    sessionTabsView.renderSessionTabs();
    await sessionTabsView.switchSession(targetSessionId, { push: false });
  } else {
    state.sessionId = targetSessionId || null;
    state.humanAssistAccessKey = state.sessionId ? (shared._humanAssistKeyBySession[state.sessionId] || null) : null;
    sessionTabsView.renderSessionTabs();
    if (state.sessionId) {
      sse.connectSSE(state.sessionId);
      await sse.pollStatus();
    } else {
      updateStepIndicator();
      sse.startStatusPolling();
    }
  }

  // URL에서 이슈/뷰 모드 복원
  if (urlSessionValid && state.sessionId === urlState.sessionId) {
    if (urlState.mainTab && urlState.mainTab !== state.mainTab) {
      switchMainTab(urlState.mainTab, { push: false });
    }
    if (urlState.issueId) {
      const issueExists = state.issues.some(i => i.id === urlState.issueId);
      if (issueExists) issueListView.selectIssue(urlState.issueId, { push: false });
    }
    if (urlState.opinionId) {
      setTimeout(() => _scrollToOpinion(urlState.opinionId), 200);
    }
  }

  // Initialize resize handlers and router
  initResize();
  initReviewersResize();
  initChangesSidebarResize();
  initIssueRenderGuard();
  initRouter();

  // 초기 URL 동기화
  router.sync();
}

// Start the application
init();
