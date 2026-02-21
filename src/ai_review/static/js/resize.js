// Panel Resize Handlers

import { renderGuard } from './state.js';
import { _flushDeferredIssueRender } from './utils.js';
import state from './state.js';

export function _syncLeftPaneLayout() {
  const panel = document.getElementById('file-panel');
  const splitter = document.getElementById('left-v-splitter');
  if (!panel || !splitter) return;
  const visible = panel.style.display !== 'none';
  const collapsed = panel.classList.contains('collapsed');
  if (!visible || collapsed) {
    splitter.style.display = 'none';
    panel.style.height = '';
    return;
  }
  splitter.style.display = '';
  const left = document.getElementById('left-panel');
  if (!left) return;
  const target = Math.round(left.clientHeight * 0.34);
  const clamped = Math.max(120, Math.min(800, target));
  panel.style.height = `${clamped}px`;
  state.filePanelHeight = clamped;
}

// Panel resize
export function initResize() {
  const handle = document.getElementById('resize-handle');
  const left = document.getElementById('left-panel');
  if (!left) return;
  window.addEventListener('resize', () => _syncLeftPaneLayout());
  setTimeout(() => _syncLeftPaneLayout(), 0);
  if (!handle) return;
  let startX, startW;
  handle.addEventListener('mousedown', (e) => {
    startX = e.clientX; startW = left.offsetWidth;
    handle.classList.add('active');
    const onMove = (e) => {
      left.style.width = Math.max(240, Math.min(760, startW + e.clientX - startX)) + 'px';
      _syncLeftPaneLayout();
    };
    const onUp = () => { handle.classList.remove('active'); document.removeEventListener('mousemove', onMove); document.removeEventListener('mouseup', onUp); };
    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup', onUp);
  });
}

export function initReviewersResize() {
  const handle = document.getElementById('right-side-splitter');
  const sidebar = document.getElementById('reviewers-sidebar');
  if (!handle || !sidebar) return;

  const minW = 320;
  const maxW = 900;
  const mobile = window.matchMedia('(max-width: 1200px)');

  const clamp = (w) => Math.max(minW, Math.min(maxW, Math.round(Number(w) || 420)));
  const apply = (w) => { sidebar.style.width = `${clamp(w)}px`; };

  if (!mobile.matches) apply(sidebar.offsetWidth || 420);

  let startX = 0;
  let startW = 0;
  handle.addEventListener('mousedown', (e) => {
    if (mobile.matches) return;
    e.preventDefault();
    startX = e.clientX;
    startW = sidebar.getBoundingClientRect().width;
    handle.classList.add('active');

    const onMove = (ev) => {
      apply(startW - (ev.clientX - startX));
    };
    const onUp = () => {
      handle.classList.remove('active');
      document.removeEventListener('mousemove', onMove);
      document.removeEventListener('mouseup', onUp);
    };
    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup', onUp);
  });

  const onViewportChange = () => {
    if (mobile.matches) {
      sidebar.style.width = '';
      return;
    }
    apply(sidebar.getBoundingClientRect().width || 420);
  };
  if (mobile.addEventListener) mobile.addEventListener('change', onViewportChange);
  else if (mobile.addListener) mobile.addListener(onViewportChange);
  window.addEventListener('resize', onViewportChange);
}

export function initChangesSidebarResize() {
  const handle = document.getElementById('changes-sidebar-splitter');
  const sidebar = document.getElementById('changes-sidebar');
  if (!handle || !sidebar) return;

  const minW = 200;
  const maxW = 600;

  handle.addEventListener('mousedown', (e) => {
    e.preventDefault();
    const startX = e.clientX;
    const startW = sidebar.getBoundingClientRect().width;
    handle.classList.add('active');

    const onMove = (ev) => {
      const w = Math.max(minW, Math.min(maxW, startW + (ev.clientX - startX)));
      sidebar.style.width = `${w}px`;
    };
    const onUp = () => {
      handle.classList.remove('active');
      document.removeEventListener('mousemove', onMove);
      document.removeEventListener('mouseup', onUp);
    };
    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup', onUp);
  });
}

// Prevent polling re-renders from breaking text drag selection in diff view.
export function initIssueRenderGuard() {
  const detail = document.getElementById('issue-detail');
  if (!detail) return;
  detail.addEventListener('mousedown', (e) => {
    if (e.button !== 0) return;
    const t = e.target;
    if (t && t.closest && t.closest('input, textarea, select, button, a, [contenteditable="true"]')) return;
    renderGuard._issueRenderPaused = true;
  });
  const release = () => {
    if (!renderGuard._issueRenderPaused) return;
    renderGuard._issueRenderPaused = false;
    setTimeout(() => _flushDeferredIssueRender(), 0);
  };
  document.addEventListener('mouseup', release);
  document.addEventListener('dragend', release);
  document.addEventListener('selectionchange', () => {
    if (renderGuard._issueRenderPaused || (window.getSelection && window.getSelection().toString())) return;
    _flushDeferredIssueRender();
  });
}
