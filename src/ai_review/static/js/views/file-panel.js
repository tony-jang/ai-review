import state, { _uiSaveStateToStorage } from '../state.js';
import { esc, _escapeAttr, getModelColor } from '../utils.js';
import { _getFileAgentDots } from './agent-panel.js';

let _fileOpeners = [{ id:'default', label:'기본 앱', available:true }];
let _fileHoverTimer = null;
let _fileHoverPayload = null;
let _fileFilterPopoverBound = false;
let _fileTreeAutoExpandedPath = '';

export function resetFileTreeAutoExpand() {
  _fileTreeAutoExpandedPath = '';
}
let _fileContextMenuPath = '';
let _fileContextMenuKind = 'file';
let _fileContextMenuPos = { x: 0, y: 0 };

export function toggleFilePanel() {
  const list = document.getElementById('file-list');
  const tools = document.getElementById('file-tools');
  const toggle = document.getElementById('file-toggle');
  const panel = document.getElementById('file-panel');
  _hideFileFilterPopover();
  const collapsed = list.classList.toggle('collapsed');
  if (tools) tools.classList.toggle('collapsed', collapsed);
  toggle.classList.toggle('collapsed', collapsed);
  if (panel) panel.classList.toggle('collapsed', collapsed);
  window._syncLeftPaneLayout();
}

export function onFileFilterQueryInput(value) {
  state.fileFilterQuery = String(value || '');
  renderFilePanel();
}

function _fileTypeKey(path) {
  const p = String(path || '');
  const name = p.split('/').pop() || '';
  if (!name) return '__no_ext__';
  if (name.startsWith('.') && !name.slice(1).includes('.')) return '__dotfile__';
  const dot = name.lastIndexOf('.');
  if (dot <= 0 || dot === name.length - 1) return '__no_ext__';
  return name.slice(dot).toLowerCase();
}

function _fileTypeLabel(typeKey) {
  if (typeKey === '__dotfile__') return 'dotfile';
  if (typeKey === '__no_ext__') return 'No extension';
  return typeKey;
}

function _collectFileFilterFacets(files) {
  const typeCounts = {};
  const statusCounts = { added: 0, modified: 0, deleted: 0, renamed: 0 };
  for (const f of files || []) {
    const t = _fileTypeKey(f.path || '');
    typeCounts[t] = (typeCounts[t] || 0) + 1;
    const st = _fileStatusMeta(f).key;
    if (statusCounts[st] == null) statusCounts[st] = 0;
    statusCounts[st] += 1;
  }
  const typeOptions = Object.entries(typeCounts)
    .map(([key, count]) => ({ key, label: _fileTypeLabel(key), count }))
    .sort((a, b) => {
      const aSpecial = a.key.startsWith('__') ? 1 : 0;
      const bSpecial = b.key.startsWith('__') ? 1 : 0;
      if (aSpecial !== bSpecial) return aSpecial - bSpecial;
      if (a.label === b.label) return b.count - a.count;
      return a.label.localeCompare(b.label);
    });
  return { typeOptions, statusCounts };
}

export function _syncFileFilterState(files) {
  const facets = _collectFileFilterFacets(files);
  const active = {};
  for (const t of facets.typeOptions) active[t.key] = (state.fileFilterTypes[t.key] !== false);
  state.fileFilterTypes = active;
  for (const key of ['added', 'modified', 'deleted', 'renamed']) {
    if (state.fileFilterStatus[key] == null) state.fileFilterStatus[key] = true;
  }
  return facets;
}

function _isStructuredFileFilterActive(facets = null) {
  const f = facets || _collectFileFilterFacets(state.files);
  const typeTotal = f.typeOptions.length;
  const typeSelected = f.typeOptions.filter(t => state.fileFilterTypes[t.key] !== false).length;
  const statusKeys = ['added', 'modified', 'deleted', 'renamed'];
  const statusSelected = statusKeys.filter(k => state.fileFilterStatus[k] !== false).length;
  return (typeTotal > 0 && typeSelected < typeTotal) || statusSelected < statusKeys.length;
}

function _hideFileFilterPopover() {
  const pop = document.getElementById('file-filter-popover');
  if (!pop) return;
  pop.classList.remove('show');
  pop.style.left = '-9999px';
  pop.style.top = '-9999px';
}

function _positionFileFilterPopover() {
  const pop = document.getElementById('file-filter-popover');
  const btn = document.getElementById('file-filter-mode-btn');
  if (!pop || !btn) return;
  const b = btn.getBoundingClientRect();
  pop.classList.add('show');
  pop.style.left = '0px';
  pop.style.top = '0px';
  const rect = pop.getBoundingClientRect();
  const left = Math.max(8, Math.min(b.right - rect.width, window.innerWidth - rect.width - 8));
  const top = Math.max(8, Math.min(b.bottom + 6, window.innerHeight - rect.height - 8));
  pop.style.left = `${left}px`;
  pop.style.top = `${top}px`;
}

function _renderFileFilterPopover() {
  const pop = document.getElementById('file-filter-popover');
  if (!pop) return;
  const facets = _syncFileFilterState(state.files);
  const statusItems = [
    { key: 'added', label: 'Added' },
    { key: 'modified', label: 'Modified' },
    { key: 'deleted', label: 'Deleted' },
    { key: 'renamed', label: 'Renamed' },
  ];
  const statusKeys = statusItems.map(s => s.key);
  const allTypesChecked = facets.typeOptions.every(t => state.fileFilterTypes[t.key] !== false);
  const allStatusesChecked = statusKeys.every(k => state.fileFilterStatus[k] !== false);
  const statusTotalCount = statusKeys.reduce((sum, key) => sum + (facets.statusCounts[key] || 0), 0);
  const typeRows = facets.typeOptions.map((t) => {
    const checked = state.fileFilterTypes[t.key] !== false;
    return `<button type="button" class="file-filter-item" data-act="type" data-key="${_escapeAttr(t.key)}">
      <span class="check">${checked ? '✓' : ''}</span>
      <span class="label">${esc(t.label)}</span>
      <span class="count">${t.count}</span>
    </button>`;
  }).join('');
  const statusRows = statusItems.map((s) => {
    const checked = state.fileFilterStatus[s.key] !== false;
    const count = facets.statusCounts[s.key] || 0;
    return `<button type="button" class="file-filter-item" data-act="status" data-key="${_escapeAttr(s.key)}">
      <span class="check">${checked ? '✓' : ''}</span>
      <span class="label">${esc(s.label)}</span>
      <span class="count">${count}</span>
    </button>`;
  }).join('');
  pop.innerHTML = `
    <div class="file-filter-section-title">File extensions</div>
    <button type="button" class="file-filter-item" data-act="types-all">
      <span class="check">${allTypesChecked ? '✓' : ''}</span>
      <span class="label">All extensions</span>
      <span class="count">${facets.typeOptions.length}</span>
    </button>
    ${typeRows || '<div class="file-filter-section-title">확장자 없음</div>'}
    <div class="sep"></div>
    <div class="file-filter-section-title">File status</div>
    <button type="button" class="file-filter-item" data-act="statuses-all">
      <span class="check">${allStatusesChecked ? '✓' : ''}</span>
      <span class="label">All statuses</span>
      <span class="count">${statusTotalCount}</span>
    </button>
    ${statusRows}
  `;
}

function _toggleFileFilterType(typeKey) {
  const key = String(typeKey || '');
  if (!key) return;
  state.fileFilterTypes[key] = !(state.fileFilterTypes[key] !== false);
}

function _setAllFileFilterTypes(enabled) {
  const facets = _collectFileFilterFacets(state.files);
  for (const t of facets.typeOptions) state.fileFilterTypes[t.key] = !!enabled;
}

function _setAllFileFilterStatuses(enabled) {
  for (const key of ['added', 'modified', 'deleted', 'renamed']) state.fileFilterStatus[key] = !!enabled;
}

function _toggleFileFilterStatus(statusKey) {
  const key = String(statusKey || '');
  if (!key) return;
  state.fileFilterStatus[key] = !(state.fileFilterStatus[key] !== false);
}

function _ensureFileFilterPopoverEvents() {
  if (_fileFilterPopoverBound) return;
  const pop = document.getElementById('file-filter-popover');
  if (!pop) return;

  pop.addEventListener('click', (event) => {
    const item = event.target?.closest?.('.file-filter-item');
    if (!item) return;
    event.preventDefault();
    event.stopPropagation();
    const act = item.dataset?.act || '';
    if (act === 'type') {
      _toggleFileFilterType(item.dataset?.key || '');
    } else if (act === 'status') {
      _toggleFileFilterStatus(item.dataset?.key || '');
    } else if (act === 'types-all') {
      const facets = _collectFileFilterFacets(state.files);
      const allOn = facets.typeOptions.every(t => state.fileFilterTypes[t.key] !== false);
      _setAllFileFilterTypes(!allOn);
    } else if (act === 'statuses-all') {
      const allOn = ['added', 'modified', 'deleted', 'renamed'].every(k => state.fileFilterStatus[k] !== false);
      _setAllFileFilterStatuses(!allOn);
    } else {
      return;
    }
    if (state.mainTab === 'changes' && window.renderChangesSidebar) {
      window.renderChangesSidebar();
    } else {
      renderFilePanel();
    }
    _renderFileFilterPopover();
    _positionFileFilterPopover();
  });

  document.addEventListener('click', (event) => {
    const btn = document.getElementById('file-filter-mode-btn');
    if (pop.contains(event.target) || btn?.contains(event.target)) return;
    _hideFileFilterPopover();
  });
  document.addEventListener('scroll', () => _hideFileFilterPopover(), true);
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') _hideFileFilterPopover();
  });
  window.addEventListener('resize', () => {
    if (!pop.classList.contains('show')) return;
    _positionFileFilterPopover();
  });
  window.addEventListener('blur', () => _hideFileFilterPopover());
  _fileFilterPopoverBound = true;
}

export function toggleFileFilterPopover(event) {
  event?.stopPropagation?.();
  _ensureFileFilterPopoverEvents();
  const pop = document.getElementById('file-filter-popover');
  if (!pop) return;
  if (pop.classList.contains('show')) {
    _hideFileFilterPopover();
    return;
  }
  _syncFileFilterState(state.files);
  _renderFileFilterPopover();
  _positionFileFilterPopover();
}

function _updateFileFilterUi(facets = null) {
  const input = document.getElementById('file-filter-input');
  const btn = document.getElementById('file-filter-mode-btn');
  if (input && input.value !== state.fileFilterQuery) input.value = state.fileFilterQuery;
  if (btn) {
    const currentFacets = facets || _collectFileFilterFacets(state.files);
    const active = _isStructuredFileFilterActive(currentFacets);
    btn.innerHTML = `☰${active ? '<span class="dot"></span>' : ''}`;
    btn.title = active ? '파일 필터 (적용 중)' : '파일 필터';
    btn.classList.toggle('active', active);
  }
}

export function _passesFileFilter(file) {
  const query = (state.fileFilterQuery || '').trim().toLowerCase();
  const path = String(file?.path || '');
  if (query && !path.toLowerCase().includes(query)) return false;
  const st = _fileStatusMeta(file).key;
  if (state.fileFilterStatus[st] === false) return false;
  const typeKey = _fileTypeKey(path);
  if (state.fileFilterTypes[typeKey] === false) return false;
  return true;
}

function _selectedIssueFilePath() {
  if (!state.selectedIssue) return '';
  const issue = state.issues.find(i => i.id === state.selectedIssue);
  return issue?.file || '';
}

function _isSelectedFilePath(path) {
  const filePath = String(path || '');
  if (!filePath) return false;
  if (state.mainTab === 'changes' && state.changesActiveFile === filePath) return true;
  if (state.selectedFileDiff === filePath) return true;
  return _selectedIssueFilePath() === filePath;
}

function _expandFileTreeAncestors(path) {
  const parts = String(path || '').split('/').filter(Boolean);
  if (parts.length <= 1) return;
  let acc = '';
  for (let i = 0; i < parts.length - 1; i++) {
    acc = acc ? `${acc}/${parts[i]}` : parts[i];
    state.fileTreeExpanded[acc] = true;
  }
}

function _syncSelectedFileTreeExpansion() {
  const path = state.selectedFileDiff || _selectedIssueFilePath();
  if (!path) {
    _fileTreeAutoExpandedPath = '';
    return;
  }
  if (_fileTreeAutoExpandedPath === path) return;
  _expandFileTreeAncestors(path);
  _fileTreeAutoExpandedPath = path;
}

function _fileTreeDefaultExpanded(depth) {
  return true;
}

function _isFileTreeExpanded(key, depth) {
  if (state.fileTreeExpanded[key] === true) return true;
  if (state.fileTreeExpanded[key] === false) return false;
  return _fileTreeDefaultExpanded(depth);
}

export function _sortedFileTreeChildren(node) {
  const children = Array.from(node.children?.values?.() || []);
  children.sort((a, b) => {
    if (a.kind !== b.kind) return a.kind === 'dir' ? -1 : 1;
    return a.name.localeCompare(b.name);
  });
  return children;
}

function _compressSingleChildDirChains(node, isRoot = false) {
  if (!node || node.kind !== 'dir') return node;

  for (const child of node.children.values()) {
    if (child.kind === 'dir') _compressSingleChildDirChains(child, false);
  }

  if (isRoot) return node;

  while (node.children.size === 1) {
    const onlyChild = Array.from(node.children.values())[0];
    if (!onlyChild || onlyChild.kind !== 'dir') break;
    node.name = node.name ? `${node.name}/${onlyChild.name}` : onlyChild.name;
    node.key = onlyChild.key;
    node.children = onlyChild.children;
    node.fileCount = onlyChild.fileCount;
  }
  return node;
}

export function _buildFileTree(files) {
  const root = { kind: 'dir', key: '', name: '', children: new Map(), fileCount: 0 };
  for (const file of files || []) {
    const rawPath = String(file.path || '').trim();
    if (!rawPath) continue;
    const parts = rawPath.split('/').filter(Boolean);
    if (!parts.length) continue;
    let cursor = root;
    let key = '';
    for (let i = 0; i < parts.length; i++) {
      const part = parts[i];
      key = key ? `${key}/${part}` : part;
      const isFile = i === parts.length - 1;
      if (!cursor.children.has(part)) {
        if (isFile) cursor.children.set(part, { kind: 'file', key, name: part, file: { ...file, path: rawPath } });
        else cursor.children.set(part, { kind: 'dir', key, name: part, children: new Map(), fileCount: 0 });
      }
      const node = cursor.children.get(part);
      if (isFile) {
        node.file = { ...file, path: rawPath };
      } else {
        cursor = node;
      }
    }
  }

  const countFiles = (node) => {
    if (node.kind === 'file') return 1;
    let count = 0;
    for (const child of node.children.values()) count += countFiles(child);
    node.fileCount = count;
    return count;
  };
  countFiles(root);
  _compressSingleChildDirChains(root, true);
  return root;
}

function _fileStatusMeta(file) {
  const raw = String(file?.status || '').toLowerCase();
  if (raw === 'added' || raw === 'new') return { key: 'added', icon: 'A', label: '추가' };
  if (raw === 'deleted' || raw === 'removed') return { key: 'deleted', icon: 'D', label: '삭제' };
  if (raw === 'renamed') return { key: 'renamed', icon: 'R', label: '이동/이름변경' };
  if (raw === 'modified' || raw === 'changed') return { key: 'modified', icon: 'M', label: '수정' };
  const additions = Number(file?.additions || 0);
  const deletions = Number(file?.deletions || 0);
  if (additions > 0 && deletions === 0) return { key: 'added', icon: 'A', label: '추가' };
  if (deletions > 0 && additions === 0) return { key: 'deleted', icon: 'D', label: '삭제' };
  return { key: 'modified', icon: 'M', label: '수정' };
}

export function _renderFileTreeNode(node, depth) {
  if (node.kind === 'file') {
    const file = node.file || {};
    const filePath = String(file.path || node.key || '');
    const statusMeta = _fileStatusMeta(file);
    const activeClass = _isSelectedFilePath(filePath) ? ' active' : '';
    return `<div class="file-tree-row file-tree-file${activeClass}" style="--depth:${depth}" data-path="${_escapeAttr(filePath)}" data-full-path="${_escapeAttr(filePath)}" data-kind="file" onclick="onFileTreeFileClick(this)" oncontextmenu="onFileTreeFileContextMenu(event, this)" onmouseenter="onFileTreeRowEnter(event,this)" onmousemove="onFileTreeRowMove(event)" onmouseleave="onFileTreeRowLeave()">
      <span class="file-tree-twist"></span>
      <span class="file-tree-status ${statusMeta.key}" title="${_escapeAttr(statusMeta.label)}">${statusMeta.icon}</span>
      <span class="file-tree-label">${esc(node.name)}</span>${_getFileAgentDots(filePath)}
    </div>`;
  }

  const expanded = _isFileTreeExpanded(node.key, depth);
  const twisty = expanded ? '\u25BE' : '\u25B8';
  const row = `<div class="file-tree-row file-tree-dir" style="--depth:${depth}" data-key="${_escapeAttr(node.key)}" data-full-path="${_escapeAttr(node.key)}" data-kind="dir" data-expanded="${expanded ? '1' : '0'}" onclick="onFileTreeDirClick(this)" oncontextmenu="onFileTreeDirContextMenu(event, this)" onmouseenter="onFileTreeRowEnter(event,this)" onmousemove="onFileTreeRowMove(event)" onmouseleave="onFileTreeRowLeave()">
    <span class="file-tree-twist">${twisty}</span>
    <span class="file-tree-icon folder">\uD83D\uDCC1</span>
    <span class="file-tree-label">${esc(node.name)}</span>
  </div>`;
  if (!expanded) return row;
  const childrenHtml = _sortedFileTreeChildren(node).map(child => _renderFileTreeNode(child, depth + 1)).join('');
  return `${row}<div class="file-tree-children">${childrenHtml}</div>`;
}

function _joinRepoPath(root, relPath) {
  const base = String(root || '').trim();
  const rel = String(relPath || '').trim();
  if (!base) return rel;
  if (!rel) return base;
  if (base.endsWith('/') || base.endsWith('\\')) return base + rel;
  const sep = base.includes('\\') && !base.includes('/') ? '\\' : '/';
  return `${base}${sep}${rel}`;
}

function _getCurrentSessionRepoPath() {
  const current = state.sessions.find(s => s.session_id === state.sessionId);
  return current?.repo_path || '';
}

function _resolveFilePathForCopy(path) {
  const repoRoot = _getCurrentSessionRepoPath();
  return repoRoot ? _joinRepoPath(repoRoot, path) : path;
}

function _currentFileOpener() {
  const available = (_fileOpeners || []).filter(o => o && (o.available || o.id === 'default'));
  const found = available.find(o => o.id === state.fileOpenerId);
  if (found) return found;
  return available.find(o => o.id === 'default') || { id: 'default', label: '기본 앱', available: true };
}

function _setFileOpener(openerId) {
  const id = String(openerId || '').trim();
  const available = (_fileOpeners || []).filter(o => o && (o.available || o.id === 'default'));
  const picked = available.find(o => o.id === id);
  state.fileOpenerId = picked ? picked.id : 'default';
}

export async function _loadFileOpeners() {
  try {
    const r = await fetch('/api/fs/openers');
    if (!r.ok) return;
    const data = await r.json();
    const list = Array.isArray(data?.openers) ? data.openers : [];
    const normalized = [];
    for (const item of list) {
      const id = String(item?.id || '').trim();
      const label = String(item?.label || '').trim();
      if (!id || !label) continue;
      normalized.push({ id, label, available: item?.available !== false });
    }
    if (!normalized.some(o => o.id === 'default')) normalized.unshift({ id: 'default', label: '기본 앱', available: true });
    _fileOpeners = normalized;
    _setFileOpener(state.fileOpenerId || 'default');
  } catch (e) {}
}

function _hideFileHoverTooltip() {
  if (_fileHoverTimer) {
    clearTimeout(_fileHoverTimer);
    _fileHoverTimer = null;
  }
  _fileHoverPayload = null;
  const tooltip = document.getElementById('file-hover-tooltip');
  if (!tooltip) return;
  tooltip.classList.remove('show');
  tooltip.style.left = '-9999px';
  tooltip.style.top = '-9999px';
}

function _ensureFileHoverTooltip() {
  let tooltip = document.getElementById('file-hover-tooltip');
  if (tooltip) return tooltip;
  tooltip = document.createElement('div');
  tooltip.id = 'file-hover-tooltip';
  tooltip.className = 'file-hover-tooltip';
  document.body.appendChild(tooltip);
  return tooltip;
}

function _positionFileHoverTooltip(tooltip, x, y) {
  const offset = 14;
  tooltip.style.left = '0px';
  tooltip.style.top = '0px';
  const rect = tooltip.getBoundingClientRect();
  const maxLeft = Math.max(8, window.innerWidth - rect.width - 8);
  const maxTop = Math.max(8, window.innerHeight - rect.height - 8);
  const left = Math.max(8, Math.min(x + offset, maxLeft));
  const top = Math.max(8, Math.min(y + offset, maxTop));
  tooltip.style.left = `${left}px`;
  tooltip.style.top = `${top}px`;
}

export function onFileTreeRowEnter(event, el) {
  _hideFileHoverTooltip();
  const fullPath = String(el?.dataset?.fullPath || el?.dataset?.path || el?.dataset?.key || '').trim();
  if (!fullPath) return;
  _fileHoverPayload = { text: fullPath, x: event?.clientX || 0, y: event?.clientY || 0 };
  _fileHoverTimer = setTimeout(() => {
    const payload = _fileHoverPayload;
    if (!payload) return;
    const tooltip = _ensureFileHoverTooltip();
    tooltip.textContent = payload.text;
    tooltip.classList.add('show');
    _positionFileHoverTooltip(tooltip, payload.x, payload.y);
  }, 200);
}

export function onFileTreeRowMove(event) {
  if (_fileHoverPayload) {
    _fileHoverPayload.x = event?.clientX || _fileHoverPayload.x;
    _fileHoverPayload.y = event?.clientY || _fileHoverPayload.y;
  }
  const tooltip = document.getElementById('file-hover-tooltip');
  if (tooltip?.classList.contains('show')) {
    _positionFileHoverTooltip(tooltip, event?.clientX || 0, event?.clientY || 0);
  }
}

export function onFileTreeRowLeave() {
  _hideFileHoverTooltip();
}

function _hideFileContextMenu() {
  const menu = document.getElementById('file-context-menu');
  if (!menu) return;
  menu.classList.remove('show');
  menu.style.left = '-9999px';
  menu.style.top = '-9999px';
}

function _contextOpenActionLabel() {
  const opener = _currentFileOpener();
  if (opener.id === 'auto') {
    return _fileContextMenuKind === 'dir' ? '자동 도구로 폴더 열기' : '자동 도구로 파일 열기';
  }
  if (_fileContextMenuKind === 'dir') {
    return opener.id === 'default' ? '폴더 열기' : `${opener.label}로 폴더 열기`;
  }
  return opener.id === 'default' ? '파일 열기' : `${opener.label}로 파일 열기`;
}

function _renderFileContextMenuContent(menu) {
  const availableOpeners = (_fileOpeners || []).filter(o => o && (o.available || o.id === 'default'));
  const current = _currentFileOpener();
  const openerItems = availableOpeners.map((opener) => {
    const active = opener.id === current.id ? ' active' : '';
    const check = opener.id === current.id ? '✓ ' : '';
    return `<button type="button" class="file-context-item${active}" data-action="set-opener" data-opener-id="${_escapeAttr(opener.id)}">${check}${esc(opener.label)}</button>`;
  }).join('');
  menu.innerHTML = `
    <button type="button" class="file-context-item" data-action="open">${esc(_contextOpenActionLabel())}</button>
    <button type="button" class="file-context-item" data-action="copy">경로 복사</button>
    <div class="file-context-sep"></div>
    <div class="file-context-title">열기 도구</div>
    ${openerItems}`;
}

function _ensureFileContextMenu() {
  let menu = document.getElementById('file-context-menu');
  if (menu) return menu;
  menu = document.createElement('div');
  menu.id = 'file-context-menu';
  menu.className = 'file-context-menu';
  menu.addEventListener('contextmenu', (e) => e.preventDefault());
  menu.addEventListener('click', async (e) => {
    const btn = e.target?.closest?.('.file-context-item');
    if (!btn) return;
    const action = btn.dataset?.action || '';
    if (action === 'open') {
      await onFileContextMenuOpen();
      return;
    }
    if (action === 'copy') {
      await onFileContextMenuCopyPath();
      return;
    }
    if (action === 'set-opener') {
      const openerId = btn.dataset?.openerId || 'default';
      _setFileOpener(openerId);
      _renderFileContextMenuContent(menu);
      window.showToast(`열기 도구: ${_currentFileOpener().label}`);
    }
  });
  document.body.appendChild(menu);

  document.addEventListener('click', (e) => {
    if (!menu.contains(e.target)) _hideFileContextMenu();
  });
  document.addEventListener('contextmenu', (e) => {
    if (!menu.contains(e.target)) _hideFileContextMenu();
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') _hideFileContextMenu();
  });
  document.addEventListener('scroll', () => {
    _hideFileContextMenu();
    _hideFileHoverTooltip();
  }, true);
  window.addEventListener('resize', () => {
    _hideFileContextMenu();
    _hideFileHoverTooltip();
  });
  window.addEventListener('blur', () => {
    _hideFileContextMenu();
    _hideFileHoverTooltip();
  });
  return menu;
}

function _positionFileContextMenu(menu, x, y) {
  menu.style.left = '0px';
  menu.style.top = '0px';
  menu.classList.add('show');
  const rect = menu.getBoundingClientRect();
  const maxLeft = Math.max(8, window.innerWidth - rect.width - 8);
  const maxTop = Math.max(8, window.innerHeight - rect.height - 8);
  const left = Math.max(8, Math.min(x, maxLeft));
  const top = Math.max(8, Math.min(y, maxTop));
  menu.style.left = `${left}px`;
  menu.style.top = `${top}px`;
}

function _openFileTreeContextMenu(event, el, kind) {
  event.preventDefault();
  event.stopPropagation();
  _hideFileHoverTooltip();
  const path = el?.dataset?.path || el?.dataset?.key || '';
  if (!path) return;
  _fileContextMenuPath = path;
  _fileContextMenuKind = kind === 'dir' ? 'dir' : 'file';
  _fileContextMenuPos = { x: event.clientX, y: event.clientY };
  const menu = _ensureFileContextMenu();
  _renderFileContextMenuContent(menu);
  _positionFileContextMenu(menu, _fileContextMenuPos.x, _fileContextMenuPos.y);
  _loadFileOpeners().then(() => {
    if (!menu.classList.contains('show')) return;
    _renderFileContextMenuContent(menu);
    _positionFileContextMenu(menu, _fileContextMenuPos.x, _fileContextMenuPos.y);
  });
}

export function onFileTreeFileContextMenu(event, el) {
  _openFileTreeContextMenu(event, el, 'file');
}

export function onFileTreeDirContextMenu(event, el) {
  _openFileTreeContextMenu(event, el, 'dir');
}

async function _copyFileTreePath(path) {
  const filePath = String(path || '').trim();
  if (!filePath) return;
  const resolvedPath = _resolveFilePathForCopy(filePath);
  if (!navigator.clipboard || typeof navigator.clipboard.writeText !== 'function') {
    window.showToast('클립보드 복사를 지원하지 않습니다', 'error');
    return;
  }
  try {
    await navigator.clipboard.writeText(resolvedPath);
    window.showToast('파일 경로를 복사했습니다');
  } catch (e) {
    window.showToast('파일 경로 복사 실패', 'error');
  }
}

async function _openFileTreePath(path, kind = 'file') {
  const filePath = String(path || '').trim();
  if (!filePath) return;
  const opener = _currentFileOpener();
  try {
    const r = await fetch('/api/fs/open', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: filePath, session_id: state.sessionId, opener_id: opener.id }),
    });
    let data = {};
    try { data = await r.json(); } catch (e) {}
    if (!r.ok) {
      window.showToast(data.detail || '로컬 파일 열기 실패', 'error');
      return;
    }
    if (kind === 'dir') window.showToast('폴더를 열었습니다');
    else window.showToast('파일을 열었습니다');
  } catch (e) {
    window.showToast('로컬 파일 열기 실패', 'error');
  }
}

async function onFileContextMenuOpen() {
  const path = _fileContextMenuPath;
  const kind = _fileContextMenuKind;
  _hideFileContextMenu();
  await _openFileTreePath(path, kind);
}

async function onFileContextMenuCopyPath() {
  const path = _fileContextMenuPath;
  _hideFileContextMenu();
  await _copyFileTreePath(path);
}

export function onFileTreeDirClick(el) {
  _hideFileHoverTooltip();
  _hideFileContextMenu();
  const key = el?.dataset?.key || '';
  if (!key) return;
  const current = el?.dataset?.expanded === '1';
  state.fileTreeExpanded[key] = !current;
  if (state.mainTab === 'changes' && window.renderChangesSidebar) {
    window.renderChangesSidebar();
  } else {
    renderFilePanel();
  }
}

export async function onFileTreeFileClick(el) {
  _hideFileHoverTooltip();
  _hideFileContextMenu();
  const path = el?.dataset?.path || '';
  if (!path) return;
  if (window.scrollToChangesFile) {
    if (state.mainTab !== 'changes') window.switchMainTab('changes');
    window.scrollToChangesFile(path);
    return;
  }
  await filterByFile(path);
}

export function renderFilePanel() {
  _hideFileHoverTooltip();
  _hideFileContextMenu();
  const facets = _syncFileFilterState(state.files);
  _updateFileFilterUi(facets);
  const panel = document.getElementById('file-panel');
  if (!state.files.length) {
    const listEl = document.getElementById('file-list');
    const toolsEl = document.getElementById('file-tools');
    const toggleEl = document.getElementById('file-toggle');
    panel.style.display = 'none';
    panel.classList.remove('collapsed');
    listEl?.classList.remove('collapsed');
    toolsEl?.classList.remove('collapsed');
    toggleEl?.classList.remove('collapsed');
    window._syncLeftPaneLayout();
    return;
  }
  panel.style.display='';
  window._syncLeftPaneLayout();
  const filteredFiles = state.files.filter(_passesFileFilter);
  const countText = filteredFiles.length === state.files.length
    ? `${state.files.length}개`
    : `${filteredFiles.length}/${state.files.length}개`;
  document.getElementById('file-count').textContent = countText;
  const list = document.getElementById('file-list');
  _syncSelectedFileTreeExpansion();
  const tree = _buildFileTree(filteredFiles);
  const html = _sortedFileTreeChildren(tree).map(node => _renderFileTreeNode(node, 0)).join('');
  if (!html) {
    list.innerHTML = '<div class="file-tree-empty">필터 조건에 맞는 파일이 없습니다.</div>';
    return;
  }
  list.innerHTML = `<div class="file-tree-canvas">${html}</div>`;
}

export async function filterByFile(path) {
  _fileTreeAutoExpandedPath = '';
  _expandFileTreeAncestors(path);
  const match = state.issues.find(i => i.file === path);
  if (match) { window.selectIssue(match.id); return; }
  // No matching issue — show standalone diff viewer
  state.selectedIssue = null;
  state.selectedFileDiff = path;
  renderFilePanel();
  window.renderIssueList();
  await window.renderFileDiff(path);
}

function _clampFilePanelHeight(height) {
  const left = document.getElementById('left-panel');
  const splitter = document.getElementById('left-v-splitter');
  if (!left) return Math.max(120, Math.round(Number(height) || 0));
  const minFile = 120;
  const minIssue = 180;
  const splitterH = splitter?.offsetHeight || 8;
  const maxFile = Math.max(minFile, left.clientHeight - minIssue - splitterH);
  return Math.max(minFile, Math.min(maxFile, Math.round(Number(height) || minFile)));
}

function _setFilePanelHeight(height, persist = false) {
  const panel = document.getElementById('file-panel');
  if (!panel) return;
  const clamped = _clampFilePanelHeight(height);
  panel.style.height = `${clamped}px`;
  state.filePanelHeight = clamped;
  if (persist) _uiSaveStateToStorage();
}

export function startLeftPaneSplitDrag(event) {
  const panel = document.getElementById('file-panel');
  const splitter = document.getElementById('left-v-splitter');
  if (!panel || !splitter) return;
  if (panel.style.display === 'none' || panel.classList.contains('collapsed')) return;
  event.preventDefault();
  const startY = event.clientY;
  const startH = panel.getBoundingClientRect().height;
  splitter.classList.add('dragging');
  const onMove = (e) => {
    _setFilePanelHeight(startH + (e.clientY - startY), false);
  };
  const onUp = () => {
    splitter.classList.remove('dragging');
    document.removeEventListener('mousemove', onMove);
    document.removeEventListener('mouseup', onUp);
    _uiSaveStateToStorage();
  };
  document.addEventListener('mousemove', onMove);
  document.addEventListener('mouseup', onUp);
}

// Export for cross-module use
window._hideFileHoverTooltip = _hideFileHoverTooltip;
