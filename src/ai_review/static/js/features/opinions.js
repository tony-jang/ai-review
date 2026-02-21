// Opinions Feature

import state from '../state.js';
import { esc, _escapeAttr } from '../utils.js';

export async function submitOpinion(issueId, action) {
  const text = document.getElementById('comment-text')?.value?.trim();
  if (!text) { alert('의견을 작성해주세요.'); return; }
  const severity = document.getElementById('comment-severity')?.value || null;
  const mentions = Array.from(new Set((text.match(/@([A-Za-z0-9_-]+)/g) || []).map(m => m.slice(1))));
  try {
    const r = await fetch(`/api/issues/${issueId}/opinions`, {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({ model_id:'human', action, reasoning:text, suggested_severity:severity||undefined, mentions })
    });
    if (!r.ok) { const e = await r.json(); alert(e.detail||'오류가 발생했습니다'); return; }
    await window.pollStatus();
  } catch(e) { alert('제출에 실패했습니다'); }
}

export function createIssueFromFile(filePath) {
  const overlay = document.createElement('div');
  overlay.className = 'report-overlay';
  const inputStyle = 'background:var(--bg);color:var(--text);border:1px solid var(--border);border-radius:6px;padding:8px 12px;font-size:13px;font-family:inherit;width:100%';
  overlay.innerHTML = `<div class="report-card" style="max-width:500px">
    <div class="report-header"><h2>이슈 등록</h2><button class="report-close" onclick="this.closest('.report-overlay').remove()">\u2715</button></div>
    <div style="padding:20px;display:flex;flex-direction:column;gap:12px">
      <input id="new-issue-title" placeholder="제목" style="${inputStyle}">
      <div style="display:flex;gap:8px">
        <input id="new-issue-file" value="${esc(filePath)}" style="flex:1;${inputStyle}">
        <input id="new-issue-line" placeholder="라인" type="number" style="width:80px;${inputStyle}">
      </div>
      <select id="new-issue-severity" style="${inputStyle}">
        <option value="critical">심각</option>
        <option value="high">높음</option>
        <option value="medium" selected>보통</option>
        <option value="low">낮음</option>
      </select>
      <textarea id="new-issue-desc" placeholder="설명" rows="4" style="${inputStyle};resize:vertical"></textarea>
      <textarea id="new-issue-suggestion" placeholder="수정 제안 (선택)" rows="2" style="${inputStyle};resize:vertical"></textarea>
      <button class="btn btn-primary" onclick="submitNewIssue(this)" style="align-self:flex-end">등록</button>
    </div>
  </div>`;
  document.body.appendChild(overlay);
  overlay.addEventListener('click', (e) => { if (e.target === overlay) overlay.remove(); });
}

export async function submitNewIssue(btn) {
  btn.disabled = true;
  const data = {
    title: document.getElementById('new-issue-title').value.trim(),
    severity: document.getElementById('new-issue-severity').value,
    file: document.getElementById('new-issue-file').value.trim(),
    line: parseInt(document.getElementById('new-issue-line').value) || null,
    description: document.getElementById('new-issue-desc').value.trim(),
    suggestion: document.getElementById('new-issue-suggestion').value.trim(),
  };
  if (!data.title || !data.file) { alert('제목과 파일은 필수입니다'); btn.disabled = false; return; }
  try {
    const r = await fetch(`/api/sessions/${state.sessionId}/issues`, {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data),
    });
    if (!r.ok) { const e = await r.json(); alert(e.detail || '오류'); btn.disabled = false; return; }
    btn.closest('.report-overlay').remove();
    await window.pollStatus();
  } catch (e) { alert('등록 실패'); btn.disabled = false; }
}

export async function processReviews() {
  if (!state.sessionId) return;
  const btn = document.getElementById('btn-process');
  btn.disabled = true; btn.textContent = '처리 중...';
  try {
    const r = await fetch(`/api/sessions/${state.sessionId}/process`, {method:'POST'});
    if (!r.ok) { const e = await r.json(); alert(e.detail||'오류가 발생했습니다'); return; }
    await window.pollStatus();
  } catch(e) { alert('처리에 실패했습니다'); }
  btn.disabled = false; btn.textContent = '리뷰 처리';
}

export async function finishReview() {
  if (!state.sessionId) return;
  if (!confirm('리뷰 세션을 완료하시겠습니까? 최종 리포트가 생성됩니다.')) return;
  const btn = document.getElementById('btn-finish');
  btn.disabled = true; btn.textContent = '완료 중...';
  try {
    const r = await fetch(`/api/sessions/${state.sessionId}/finish`, {method:'POST'});
    if (!r.ok) { const e = await r.json(); alert(e.detail||'오류가 발생했습니다'); return; }
    const report = await r.json();
    window.showReport(report);
    await window.pollStatus();
  } catch(e) { alert('완료에 실패했습니다'); }
  btn.disabled = false; btn.textContent = '리뷰 완료';
}
