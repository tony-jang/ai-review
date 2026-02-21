// Agent Chat Feature

import state from '../state.js';
import { esc, formatTs, formatElapsed } from '../utils.js';

// Module-local state
let agentChatRuntimeInterval = null;
let agentChatLastMessageCount = 0;

export function renderAgentChatMessages(messages) {
  return messages.map((m) => {
    if (m.role === 'user') {
      return `<div class="assist-msg assist-msg-user"><div class="bubble">${esc(m.content)}</div></div>`;
    }
    return `<div class="assist-msg assist-msg-ai"><div class="bubble">${esc(m.content)}</div></div>`;
  }).join('');
}

export function renderAgentRuntime(runtime) {
  if (!runtime) return '<div class="agent-runtime-card">상태 정보를 불러오지 못했습니다.</div>';
  const pending = (runtime.pending_issue_ids || []).join(', ') || '-';
  const prompt = runtime.prompt_full ? esc(runtime.prompt_full) : (runtime.prompt_preview ? esc(runtime.prompt_preview) : '(프롬프트 미기록)');
  const out = runtime.last_output ? esc(runtime.last_output) : '(출력 없음)';
  const err = runtime.last_error ? esc(runtime.last_error) : '(오류 없음)';
  return `<div class="agent-runtime-card">
    <div class="agent-runtime-grid">
      <div><span class="k">상태:</span> <span class="v">${esc(runtime.status || '-')}</span></div>
      <div><span class="k">작업:</span> <span class="v">${esc(runtime.task_type || '-')}</span></div>
      <div><span class="k">설명:</span> <span class="v">${esc(runtime.description || '-')}</span></div>
      <div><span class="k">경과:</span> <span class="v">${runtime.elapsed_seconds != null ? esc(formatElapsed(runtime.elapsed_seconds)) : '-'}</span></div>
      <div><span class="k">대기 이슈 수:</span> <span class="v">${runtime.pending_count ?? 0}</span></div>
      <div><span class="k">대기 이슈 ID:</span> <span class="v">${esc(pending)}</span></div>
      <div><span class="k">최근 사유:</span> <span class="v">${esc(runtime.last_reason || '-')}</span></div>
      <div><span class="k">갱신 시각:</span> <span class="v">${runtime.updated_at ? esc(formatTs(runtime.updated_at)) : '-'}</span></div>
    </div>
    <div class="agent-runtime-log-title">최근 stdout</div>
    <div class="agent-runtime-log">${out}</div>
    <div class="agent-runtime-log-title">최근 stderr</div>
    <div class="agent-runtime-log">${err}</div>
    <div class="agent-runtime-log-title">프롬프트 미리보기</div>
    <div class="agent-runtime-log">${prompt}</div>
  </div>`;
}

export async function openAgentChat(modelId) {
  state.selectedAgent = modelId;
  agentChatLastMessageCount = 0;
  const overlay = document.createElement('div');
  overlay.className = 'report-overlay';
  overlay.id = 'agent-chat-overlay';
  overlay.innerHTML = `<div class="report-card" style="max-width:760px">
    <div class="report-header"><h2>@${esc(modelId)} 직접 대화</h2><button class="report-close" onclick="closeAgentChat()">\u2715</button></div>
    <div id="agent-runtime"></div>
    <div class="assist-messages" id="agent-chat-messages" style="max-height:55vh"></div>
    <div class="assist-input-bar">
      <textarea class="assist-input" id="agent-chat-input" placeholder="@${esc(modelId)} 에게 메시지를 입력하세요..." rows="2" onkeydown="if(event.key==='Enter'&&!event.shiftKey){event.preventDefault();sendAgentChat()}"></textarea>
      <button class="btn-assist-send" id="agent-chat-send" onclick="sendAgentChat()">전송</button>
    </div>
  </div>`;
  document.body.appendChild(overlay);
  overlay.addEventListener('click', (e) => { if (e.target === overlay) closeAgentChat(); });
  await refreshAgentChat();
  if (agentChatRuntimeInterval) clearInterval(agentChatRuntimeInterval);
  agentChatRuntimeInterval = setInterval(() => {
    if (!state.selectedAgent || !document.getElementById('agent-chat-overlay')) {
      clearInterval(agentChatRuntimeInterval);
      agentChatRuntimeInterval = null;
      return;
    }
    refreshAgentChat();
  }, 3000);
}

export function closeAgentChat() {
  document.getElementById('agent-chat-overlay')?.remove();
  if (agentChatRuntimeInterval) {
    clearInterval(agentChatRuntimeInterval);
    agentChatRuntimeInterval = null;
  }
  agentChatLastMessageCount = 0;
  state.selectedAgent = null;
}

export async function refreshAgentChat() {
  if (!state.selectedAgent) return;
  const [chatResp, runtimeResp] = await Promise.all([
    fetch(`/api/sessions/current/agents/${state.selectedAgent}/chat`),
    fetch(`/api/sessions/current/agents/${state.selectedAgent}/runtime`),
  ]);
  const data = await chatResp.json();
  if (!chatResp.ok) return;
  const el = document.getElementById('agent-chat-messages');
  if (!el) return;
  const prevCount = agentChatLastMessageCount;
  const nextCount = data.messages?.length || 0;
  const wasNearBottom = (el.scrollHeight - el.scrollTop - el.clientHeight) < 24;
  el.innerHTML = data.messages?.length ? renderAgentChatMessages(data.messages) : '<div style="padding:8px;color:var(--text-muted);font-size:13px;text-align:center">아직 대화가 없습니다</div>';
  if (nextCount > prevCount || wasNearBottom) {
    el.scrollTop = el.scrollHeight;
  }
  agentChatLastMessageCount = nextCount;
  const runtimeEl = document.getElementById('agent-runtime');
  if (runtimeEl) {
    const oldLogs = Array.from(runtimeEl.querySelectorAll('.agent-runtime-log'));
    const oldScrolls = oldLogs.map((node) => node.scrollTop);
    if (runtimeResp.ok) {
      const runtime = await runtimeResp.json();
      runtimeEl.innerHTML = renderAgentRuntime(runtime);
    } else {
      runtimeEl.innerHTML = renderAgentRuntime(null);
    }
    const newLogs = Array.from(runtimeEl.querySelectorAll('.agent-runtime-log'));
    newLogs.forEach((node, idx) => {
      if (oldScrolls[idx] != null) node.scrollTop = oldScrolls[idx];
    });
  }
}

export async function sendAgentChat() {
  if (!state.selectedAgent) return;
  const input = document.getElementById('agent-chat-input');
  const send = document.getElementById('agent-chat-send');
  const message = input?.value?.trim();
  if (!message) return;
  send.disabled = true;
  try {
    const r = await fetch(`/api/sessions/current/agents/${state.selectedAgent}/chat`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ message }),
    });
    const data = await r.json();
    if (!r.ok) { alert(data.detail || '전송 실패'); return; }
    input.value = '';
    const el = document.getElementById('agent-chat-messages');
    if (el) {
      el.innerHTML = renderAgentChatMessages(data.messages || []);
      el.scrollTop = el.scrollHeight;
    }
  } catch (e) {
    alert('전송 실패');
  } finally {
    send.disabled = false;
  }
}
