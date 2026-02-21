// Report Modal

import { SEVERITY_COLORS, SEVERITY_ICONS } from '../constants.js';
import { sevLabel, esc } from '../utils.js';

export function showReport(report) {
  const overlay = document.createElement('div');
  overlay.className = 'report-overlay';
  const s = report.stats || {};
  let issuesHtml = '';
  for (const i of (report.issues||[])) {
    const sev = i.final_severity || 'low';
    const sevColor = SEVERITY_COLORS[sev]||'#6B7280';
    const icon = SEVERITY_ICONS[sev]||'\u26AA';
    const cIcon = i.consensus ? '\u2705' : '\u26A0';
    issuesHtml += `<div class="report-issue">
      <span>${icon}</span>
      <span class="severity-badge" style="background:${sevColor}20;color:${sevColor}">${sevLabel(sev)}</span>
      <span style="flex:1">${esc(i.title)}</span>
      <span style="color:var(--text-muted);font-size:11px">${esc(i.file||'')}</span>
      <span>${cIcon}</span>
    </div>`;
  }
  overlay.innerHTML = `<div class="report-card">
    <div class="report-header"><h2>최종 리포트</h2><button class="report-close" onclick="this.closest('.report-overlay').remove()">\u2715</button></div>
    <div class="report-stats">
      <div class="report-stat"><div class="value">${s.total_issues_found||0}</div><div class="label">원본 이슈</div></div>
      <div class="report-stat"><div class="value">${s.after_dedup||0}</div><div class="label">중복 제거 후</div></div>
      <div class="report-stat"><div class="value">${s.consensus_reached||0}</div><div class="label">합의 완료</div></div>
      <div class="report-stat"><div class="value">${s.dismissed||0}</div><div class="label">기각</div></div>
    </div>
    <div class="report-issues">${issuesHtml || '<div style="padding:12px;color:var(--text-muted)">이슈 없음</div>'}</div>
  </div>`;
  document.body.appendChild(overlay);
  overlay.addEventListener('click', (e) => { if (e.target === overlay) overlay.remove(); });
}

export function showToast(message, kind = 'success', durationMs = null) {
  if (!message) return;
  const resolvedDuration = Number.isFinite(durationMs) && durationMs > 0
    ? Number(durationMs)
    : (kind === 'error' ? 5200 : 3800);
  let stack = document.getElementById('toast-stack');
  if (!stack) {
    stack = document.createElement('div');
    stack.id = 'toast-stack';
    stack.className = 'toast-stack';
    document.body.appendChild(stack);
  }
  const toastKey = `${kind || 'success'}::${String(message).trim()}`;
  let toast = Array.from(stack.children).find((el) => el?.dataset?.toastKey === toastKey);
  if (!toast) {
    if (stack.children.length >= 3) stack.firstElementChild?.remove();
    toast = document.createElement('div');
    toast.className = `toast ${kind || 'success'}`;
    toast.dataset.toastKey = toastKey;
    toast.innerHTML = `<span class="toast-icon">${kind === 'error' ? '⚠' : '✓'}</span><span class="toast-msg"></span>`;
    stack.appendChild(toast);
  } else {
    toast.className = `toast ${kind || 'success'}`;
  }
  const msgEl = toast.querySelector('.toast-msg');
  if (msgEl) msgEl.textContent = String(message);
  if (toast._hideTimer) clearTimeout(toast._hideTimer);
  if (toast._removeTimer) clearTimeout(toast._removeTimer);
  requestAnimationFrame(() => toast.classList.add('show'));
  toast._hideTimer = setTimeout(() => {
    toast.classList.remove('show');
    toast._removeTimer = setTimeout(() => toast.remove(), 180);
  }, resolvedDuration);
}
