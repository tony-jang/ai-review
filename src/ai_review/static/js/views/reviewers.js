import state, { shared } from '../state.js';
import { _normalizeReviewerAction } from '../utils.js';
import { issueGroupKey } from './issue-list.js';

export function toggleReviewerIssues(modelId) {
  const key = String(modelId || '').trim();
  if (!key) return;
  state.reviewerExpanded[key] = !state.reviewerExpanded[key];
  window.renderAgentPanel();
}

export function openReviewerChat(modelId, event) {
  if (event?.preventDefault) event.preventDefault();
  if (event?.stopPropagation) event.stopPropagation();
  const key = String(modelId || '').trim();
  if (!key) return;
  window.openAgentChat(key);
}

export function _flashIssueJump(issueId) {
  const key = String(issueId || '').trim();
  if (!key) return;
  const row = document.querySelector(`.issue-item[data-issue-id="${key}"]`);
  if (row) {
    row.scrollIntoView({ block: 'center' });
    row.classList.remove('issue-item-flash');
    void row.offsetWidth;
    row.classList.add('issue-item-flash');
    setTimeout(() => row.classList.remove('issue-item-flash'), 1050);
  }
  const header = document.querySelector('#issue-detail .detail-header-bar');
  if (header) {
    header.classList.remove('detail-flash');
    void header.offsetWidth;
    header.classList.add('detail-flash');
    setTimeout(() => header.classList.remove('detail-flash'), 1000);
  }
}

export function _expandIssueGroupForIssue(issueId) {
  const key = String(issueId || '').trim();
  if (!key) return;
  const issue = state.issues.find(i => i.id === key);
  if (!issue) return;
  state.collapsedIssueGroups[issueGroupKey(issue)] = false;
}

export function _findOpinionNode(modelId, timestamp, action) {
  const modelKey = String(modelId || '').trim();
  if (!modelKey) return null;
  const timeKey = String(timestamp || '').trim();
  const actionKey = _normalizeReviewerAction(action);
  const nodes = Array.from(document.querySelectorAll('#issue-detail .timeline-item, #issue-detail .opinion'));
  if (!nodes.length) return null;
  const byModel = nodes.filter((node) => String(node.dataset.opModel || '').trim() === modelKey);
  if (timeKey) {
    const exact = byModel.find((node) => String(node.dataset.opTime || '').trim() === timeKey);
    if (exact) return exact;
  }
  const byAction = byModel.find((node) => _normalizeReviewerAction(node.dataset.opAction || '') === actionKey);
  if (byAction) return byAction;
  return byModel[0] || null;
}

export function _flashOpinionNode(node) {
  if (!node) return;
  node.classList.remove('comment-flash');
  void node.offsetWidth;
  node.classList.add('comment-flash');
  setTimeout(() => node.classList.remove('comment-flash'), 1250);
}

export function _applyPendingOpinionJump(issueId) {
  const pending = shared._pendingOpinionJump;
  if (!pending) return;
  if (String(pending.issueId || '') !== String(issueId || '')) return;
  shared._pendingOpinionJump = null;
  setTimeout(() => {
    const node = _findOpinionNode(pending.modelId, pending.timestamp, pending.action);
    if (!node) return;
    node.scrollIntoView({ block: 'center' });
    _flashOpinionNode(node);
  }, 0);
}

export function jumpToIssueFromReviewer(issueId, modelId, timestamp = '', action = 'raise') {
  const key = String(issueId || '').trim();
  if (!key) return;
  if (!state.issues.some(i => i.id === key)) return;
  _expandIssueGroupForIssue(key);
  state.issueDetailModeByIssue[key] = 'timeline';
  shared._pendingOpinionJump = {
    issueId: key,
    modelId: String(modelId || '').trim(),
    timestamp: String(timestamp || '').trim(),
    action: String(action || 'raise').toLowerCase(),
  };
  window.selectIssue(key);
}

export function jumpToIssueFromMention(ref) {
  const key = (ref || '').trim();
  if (!key) return;
  const exact = state.issues.find(i => i.id === key);
  if (exact) {
    _expandIssueGroupForIssue(exact.id);
    window.selectIssue(exact.id);
    setTimeout(() => _flashIssueJump(exact.id), 0);
    return;
  }
  const matches = state.issues.filter(i => i.id.startsWith(key));
  if (matches.length === 1) {
    _expandIssueGroupForIssue(matches[0].id);
    window.selectIssue(matches[0].id);
    setTimeout(() => _flashIssueJump(matches[0].id), 0);
    return;
  }
  if (matches.length > 1) {
    window.showToast(`@${key}가 여러 이슈와 매칭됩니다. 더 길게 입력해주세요.`, 'error');
    return;
  }
  window.showToast(`@${key}에 해당하는 이슈를 찾지 못했습니다.`, 'error');
}

export function jumpToAgentFromMention(ref) {
  const key = (ref || '').trim();
  if (!key) return;
  const agent = window.findAgentByRef(key);
  if (!agent || !agent.model_id) {
    window.showToast(`@${key}에 해당하는 에이전트를 찾지 못했습니다.`, 'error');
    return;
  }
  window.openAgentChat(agent.model_id);
}
