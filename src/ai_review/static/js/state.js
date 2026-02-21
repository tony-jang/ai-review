const state = {
  sessionId:null,
  sessions:[],
  status:'idle',
  currentTurn:0,
  mainTab: 'conversation',
  issues:[],
  humanAssistAccessKey:null,
  issueNumberById:{},
  nextIssueNumber:1,
  selectedIssue:null,
  selectedAgent:null,
  selectedFileDiff:null,
  implementationContext: null,
  reviews: [],
  diffCache:{},
  files:[],
  fileFilterQuery:'',
  fileFilterTypes:{},
  fileFilterStatus:{ added:true, modified:true, deleted:true, renamed:true },
  filePanelHeight:0,
  fileOpenerId:'auto',
  fileTreeExpanded:{},
  changesActiveFile:null,
  agents:[],
  expandedDiffByIssue:{},
  collapsedIssueGroups:{},
  issueDetailModeByIssue:{},
  expandedReasoning:{},
  reviewerExpanded:{},
};

export default state;

export const UI_STATE_STORAGE_KEY = 'ai-review-ui-state-v1';

export const uiState = {
  _uiHydrating: false,
  _uiSelectedIssueBySession: {},
  _uiSavedFilters: null,
};

export const renderGuard = {
  _issueRenderPaused: false,
  _issueRenderPending: false,
  _pollDeferredWhileSelecting: false,
  _issueDetailRenderSeq: 0,
};

export const shared = {
  _humanAssistKeyBySession: {},
  _pendingOpinionJump: null,
};

export function _uiReadFiltersFromDom() {
  return {
    severity: document.getElementById('filter-severity')?.value || '',
    agent: document.getElementById('filter-agent')?.value || '',
    consensus: document.getElementById('filter-consensus')?.value || '',
    mine_only: !!document.getElementById('filter-mine')?.checked,
    core_only: !!document.getElementById('filter-core-only')?.checked,
  };
}

export function _uiApplyFiltersToDom(filters) {
  const f = filters || {};
  const sevEl = document.getElementById('filter-severity');
  const agEl = document.getElementById('filter-agent');
  const conEl = document.getElementById('filter-consensus');
  const mineEl = document.getElementById('filter-mine');
  const coreEl = document.getElementById('filter-core-only');
  if (sevEl) sevEl.value = f.severity || '';
  if (agEl) agEl.value = f.agent || '';
  if (conEl) conEl.value = f.consensus || '';
  if (mineEl) mineEl.checked = !!f.mine_only;
  if (coreEl) coreEl.checked = f.core_only === false ? false : true;
}

export function _uiLoadStateFromStorage() {
  try {
    const raw = localStorage.getItem(UI_STATE_STORAGE_KEY);
    if (!raw) return {};
    const data = JSON.parse(raw);
    return (data && typeof data === 'object') ? data : {};
  } catch (e) {
    return {};
  }
}

export function _uiSaveStateToStorage() {
  if (uiState._uiHydrating) return;
  if (state.sessionId) {
    if (state.selectedIssue) uiState._uiSelectedIssueBySession[state.sessionId] = state.selectedIssue;
    else delete uiState._uiSelectedIssueBySession[state.sessionId];
  }

  const payload = {
    session_id: state.sessionId || '',
    selected_issue_by_session: uiState._uiSelectedIssueBySession,
    filters: _uiReadFiltersFromDom(),
    issue_detail_mode_by_issue: state.issueDetailModeByIssue || {},
    file_panel_height: Number(state.filePanelHeight) || 0,
  };
  uiState._uiSavedFilters = payload.filters;
  try {
    localStorage.setItem(UI_STATE_STORAGE_KEY, JSON.stringify(payload));
  } catch (e) {}
}

export function _uiRestoreStateFromStorage() {
  uiState._uiHydrating = true;
  const saved = _uiLoadStateFromStorage();
  uiState._uiSelectedIssueBySession = saved.selected_issue_by_session || {};
  state.issueDetailModeByIssue = saved.issue_detail_mode_by_issue || {};
  {
    const h = Number(saved.file_panel_height || 0);
    state.filePanelHeight = Number.isFinite(h) && h > 0 ? h : 0;
  }
  uiState._uiSavedFilters = saved.filters || null;
  _uiApplyFiltersToDom(uiState._uiSavedFilters || {});
  uiState._uiHydrating = false;
  return saved;
}
