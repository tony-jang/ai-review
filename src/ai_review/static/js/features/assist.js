// Assist (Issue Resolution Helper)

import state, { shared } from '../state.js';
import { esc, renderMd } from '../utils.js';

export function renderAssistMessages(messages) {
  return messages.map(m => {
    if (m.role === 'user') {
      return `<div class="assist-msg assist-msg-user"><div class="bubble">${esc(m.content)}</div></div>`;
    }
    return `<div class="assist-msg assist-msg-ai"><div class="bubble">${renderMd(m.content)}</div></div>`;
  }).join('');
}

export function toggleAssist(issueId) {
  const chat = document.getElementById('assist-chat-' + issueId);
  if (chat) chat.classList.toggle('open');
}

export async function sendAssist(issueId, message) {
  if (!message?.trim()) return;
  const messagesEl = document.getElementById('assist-messages-' + issueId);
  const sendBtn = document.getElementById('assist-send-' + issueId);
  const inputEl = document.getElementById('assist-input-' + issueId);

  // Show user message immediately
  messagesEl.innerHTML += `<div class="assist-msg assist-msg-user"><div class="bubble">${esc(message)}</div></div>`;
  // Show thinking
  messagesEl.innerHTML += '<div class="assist-thinking" id="assist-thinking"><span>생각하는 중</span><span class="dots"><span></span><span></span><span></span></span></div>';
  messagesEl.scrollTop = messagesEl.scrollHeight;

  if (sendBtn) sendBtn.disabled = true;
  if (inputEl) inputEl.value = '';

  try {
    const r = await fetch(`/api/issues/${issueId}/assist`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message }),
    });
    const data = await r.json();
    if (!r.ok) { alert(data.detail || '오류가 발생했습니다'); return; }

    // Re-render all messages from server response
    messagesEl.innerHTML = renderAssistMessages(data.messages);

    // Update CLI command if available
    if (data.cli_command) {
      const cliEl = document.getElementById('assist-cli-' + issueId);
      if (cliEl) cliEl.textContent = data.cli_command;
    }
  } catch (e) {
    // Remove thinking indicator
    const thinking = document.getElementById('assist-thinking');
    if (thinking) thinking.remove();
    messagesEl.innerHTML += `<div class="assist-msg assist-msg-ai"><div class="bubble" style="color:var(--severity-critical)">요청 실패: ${esc(e.message)}</div></div>`;
  } finally {
    if (sendBtn) sendBtn.disabled = false;
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }
}

export async function issueHumanAssistKey() {
  if (!state.sessionId) {
    window.showToast('활성 세션이 없습니다', 'error');
    return '';
  }
  try {
    const r = await fetch(`/api/sessions/${state.sessionId}/assist/key`, { method: 'POST' });
    const data = await r.json();
    if (!r.ok) {
      window.showToast(data.detail || '도우미 키 발급 실패', 'error');
      return '';
    }
    const key = String(data.access_key || '').trim();
    if (!key) {
      window.showToast('도우미 키 응답이 비어 있습니다', 'error');
      return '';
    }
    state.humanAssistAccessKey = key;
    shared._humanAssistKeyBySession[state.sessionId] = key;
    window.showToast('human-assist 접근 키가 발급되었습니다', 'success');
    return key;
  } catch (e) {
    window.showToast('도우미 키 발급 실패', 'error');
    return '';
  }
}

export async function submitAssistOpinion(issueId) {
  const input = document.getElementById('assist-input-' + issueId);
  const msg = input?.value?.trim() || '';
  let key = (state.humanAssistAccessKey || '').trim();
  if (!key && state.sessionId) key = (shared._humanAssistKeyBySession[state.sessionId] || '').trim();
  if (!key) {
    alert('먼저 도우미 키를 발급해주세요.');
    return;
  }
  try {
    const r = await fetch(`/api/issues/${issueId}/assist/opinion`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Agent-Key': key },
      body: JSON.stringify({ message: msg }),
    });
    const data = await r.json();
    if (!r.ok) {
      if (r.status === 403) {
        state.humanAssistAccessKey = null;
        if (state.sessionId) delete shared._humanAssistKeyBySession[state.sessionId];
      }
      alert(data.detail || 'AI 의견 제출 실패');
      return;
    }
    if (input) input.value = '';
    await window.pollStatus();
    if (state.selectedIssue === issueId) window.renderMainTabContent();
  } catch (e) {
    alert('AI 의견 제출 실패');
  }
}

export function sendAssistFromInput(issueId) {
  const input = document.getElementById('assist-input-' + issueId);
  if (!input?.value?.trim()) return;
  sendAssist(issueId, input.value.trim());
}

export function copyCliCommand(issueId) {
  const el = document.getElementById('assist-cli-' + issueId);
  if (!el) return;
  navigator.clipboard.writeText(el.textContent).then(() => {
    const hint = el.parentElement.querySelector('.copy-hint');
    if (hint) { hint.textContent = '복사됨!'; setTimeout(() => { hint.textContent = '복사'; }, 1500); }
  });
}
