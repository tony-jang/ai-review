import { esc, _isIssueTargetLine, _issueLineRange, _issueRangeLabel } from '../utils.js';
import state from '../state.js';
import { _highlightDiffCode, _requestDiffHighlighterRefresh, _guessDiffLanguage } from './highlighter.js';
import { parseDiffLines } from './parser.js';

function _renderDiffLineContent(prefix, text, language, enableHighlight = true) {
  const highlighted = _highlightDiffCode(text, language, enableHighlight);
  return `<span class="diff-prefix">${esc(prefix)}</span><span class="diff-code">${highlighted}</span>`;
}

function _findFileInfo(path) {
  const filePath = String(path || '');
  if (!filePath) return null;
  return state.files.find(f => f.path === filePath) || null;
}

function _isModifiedFileDiff(filePath, rows) {
  const info = _findFileInfo(filePath);
  if (info) {
    const adds = Number(info.additions || 0);
    const dels = Number(info.deletions || 0);
    return adds > 0 && dels > 0;
  }
  let hasAdd = false;
  let hasDel = false;
  for (const row of rows || []) {
    if (row.type === 'add') hasAdd = true;
    if (row.type === 'del') hasDel = true;
    if (hasAdd && hasDel) return true;
  }
  return false;
}

export function _renderDiffStatsMeta(filePath) {
  const info = _findFileInfo(filePath);
  if (!info) return '';
  const adds = Number(info.additions || 0);
  const dels = Number(info.deletions || 0);
  return `<span class="diff-file-meta"><span class="add">+${adds}</span><span class="del">-${dels}</span></span>`;
}

function _renderUnifiedDiffTable(rows, issueRef, language, enableHighlight) {
  let html = '<table class="diff-table">';
  for (const l of rows) {
    if (l.type === 'hunk') {
      html += `<tr class="diff-hunk"><td colspan="3">${esc(l.text)}</td></tr>`;
      continue;
    }
    const cls = l.type === 'add' ? 'diff-add' : l.type === 'del' ? 'diff-del' : 'diff-context';
    const marker = _isIssueTargetLine(issueRef, l.new) ? ' diff-issue-marker' : '';
    const prefix = l.type === 'add' ? '+' : l.type === 'del' ? '-' : ' ';
    const content = _renderDiffLineContent(prefix, l.text, language, enableHighlight);
    html += `<tr class="${cls}${marker}"><td class="diff-line-num">${l.old}</td><td class="diff-line-num">${l.new}</td><td class="diff-line-content">${content}</td></tr>`;
  }
  html += '</table>';
  return html;
}

function _hasVeryLongToken(text, limit = 140) {
  const raw = String(text || '');
  if (!raw) return false;
  return raw.split(/\s+/).some(token => token.length >= limit);
}

function _shouldStackSplitPair(leftLine, rightLine) {
  if (!leftLine || !rightLine) return false;
  const leftText = String(leftLine.text || '');
  const rightText = String(rightLine.text || '');
  if ((leftText.length + rightText.length) >= 180) return true;
  if (Math.max(leftText.length, rightText.length) >= 220) return true;
  return _hasVeryLongToken(leftText) || _hasVeryLongToken(rightText);
}

function _renderSplitDiffTable(rows, issueRef, language, enableHighlight) {
  let html = '<table class="diff-table diff-split-table"><colgroup><col style="width:46px"><col style="width:calc(50% - 46px)"><col style="width:46px"><col style="width:calc(50% - 46px)"></colgroup>';
  let i = 0;

  const splitRow = (leftLine, rightLine, extraClass = '') => {
    const leftNum = leftLine ? leftLine.old : '';
    const rightNum = rightLine ? rightLine.new : '';
    const leftCls = leftLine ? (leftLine.type === 'del' ? 'diff-del' : 'diff-context') : 'diff-empty';
    const rightCls = rightLine ? (rightLine.type === 'add' ? 'diff-add' : 'diff-context') : 'diff-empty';
    const marker = _isIssueTargetLine(issueRef, rightLine?.new) ? ' diff-issue-marker' : '';
    const leftContent = leftLine
      ? _renderDiffLineContent(leftLine.type === 'del' ? '-' : ' ', leftLine.text, language, enableHighlight)
      : '';
    const rightContent = rightLine
      ? _renderDiffLineContent(rightLine.type === 'add' ? '+' : ' ', rightLine.text, language, enableHighlight)
      : '';
    const trCls = [extraClass.trim(), marker.trim()].filter(Boolean).join(' ');
    return `<tr${trCls ? ` class="${trCls}"` : ''}>
      <td class="diff-line-num diff-split-old-num ${leftCls}">${leftNum}</td>
      <td class="diff-line-content diff-split-old-content ${leftCls}">${leftContent}</td>
      <td class="diff-line-num diff-split-new-num ${rightCls}${marker}">${rightNum}</td>
      <td class="diff-line-content diff-split-new-content ${rightCls}${marker}">${rightContent}</td>
    </tr>`;
  };

  const splitRowPair = (leftLine, rightLine) => {
    if (_shouldStackSplitPair(leftLine, rightLine)) {
      return (
        splitRow(leftLine, null, 'diff-split-row-stack') +
        splitRow(null, rightLine, 'diff-split-row-stack')
      );
    }
    return splitRow(leftLine, rightLine);
  };

  while (i < rows.length) {
    const line = rows[i];
    if (line.type === 'hunk') {
      html += `<tr class="diff-hunk"><td colspan="4">${esc(line.text)}</td></tr>`;
      i += 1;
      continue;
    }
    if (line.type === 'ctx') {
      html += splitRow(line, line);
      i += 1;
      continue;
    }

    const delRows = [];
    const addRows = [];
    while (i < rows.length && rows[i].type !== 'ctx' && rows[i].type !== 'hunk') {
      if (rows[i].type === 'del') delRows.push(rows[i]);
      else if (rows[i].type === 'add') addRows.push(rows[i]);
      i += 1;
    }
    const blockLen = Math.max(delRows.length, addRows.length);
    for (let j = 0; j < blockLen; j++) {
      html += splitRowPair(delRows[j] || null, addRows[j] || null);
    }
  }

  html += '</table>';
  return html;
}

export function _renderDiffRows(rows, issueRef, language, enableHighlight, filePath) {
  _requestDiffHighlighterRefresh();
  if (_isModifiedFileDiff(filePath, rows)) return _renderSplitDiffTable(rows, issueRef, language, enableHighlight);
  return _renderUnifiedDiffTable(rows, issueRef, language, enableHighlight);
}

export function renderDiff(diffContent, issue) {
  if (!diffContent) return '<div class="diff-loading">변경 내역 없음</div>';
  const lines = parseDiffLines(diffContent);
  if (!lines.length) return '<div class="diff-loading">변경 없음</div>';
  const filePath = issue?.file || state.selectedFileDiff || '';
  const language = _guessDiffLanguage(filePath);
  const enableHighlight = !!(language && lines.length <= 1200);
  return _renderDiffRows(lines, issue, language, enableHighlight, filePath);
}

export function renderSourceLines(data, issue) {
  if (!data || !data.lines || !data.lines.length) return '';
  const filePath = issue?.file || '';
  const language = _guessDiffLanguage(filePath);
  const enableHighlight = !!(language && data.lines.length <= 1200);
  let html = '<table class="diff-table">';
  for (const l of data.lines) {
    const marker = _isIssueTargetLine(issue, l.number) ? ' diff-issue-marker' : '';
    const content = `<span class="diff-code">${_highlightDiffCode(l.content, language, enableHighlight)}</span>`;
    html += `<tr class="diff-context${marker}"><td class="diff-line-num"></td><td class="diff-line-num">${l.number}</td><td class="diff-line-content">${content}</td></tr>`;
  }
  html += '</table>';
  _requestDiffHighlighterRefresh();
  return html;
}

export function diffContainsLine(diffContent, lineNo) {
  if (!diffContent || lineNo === null) return false;
  const lines = parseDiffLines(diffContent);
  return lines.some(l => l.type !== 'hunk' && Number.isInteger(l.new) && l.new === lineNo);
}

export function renderDiffWithFocus(diffContent, issue, contextLines=20, full=false) {
  const target = _issueLineRange(issue);
  if (full || target.start === null) return renderDiff(diffContent, issue);
  const lines = parseDiffLines(diffContent);
  if (!lines.length) return '<div class="diff-loading">변경 없음</div>';
  const filePath = issue?.file || state.selectedFileDiff || '';
  const language = _guessDiffLanguage(filePath);
  const enableHighlight = !!(language && lines.length <= 1200);
  const end = target.end ?? target.start;
  const focusedIndex = new Set();

  for (let idx = 0; idx < lines.length; idx++) {
    const l = lines[idx];
    if (l.type === 'hunk') continue;
    const newLine = Number.isInteger(l.new) ? l.new : null;
    if (newLine !== null && newLine >= (target.start - contextLines) && newLine <= (end + contextLines)) {
      focusedIndex.add(idx);
    }
  }

  for (let idx = 0; idx < lines.length; idx++) {
    const l = lines[idx];
    if (l.type !== 'del') continue;
    if (focusedIndex.has(idx - 1) || focusedIndex.has(idx + 1)) focusedIndex.add(idx);
  }

  if (!focusedIndex.size) return renderDiff(diffContent, issue);
  const focused = lines.filter((_, idx) => focusedIndex.has(idx) && lines[idx].type !== 'hunk');
  if (!focused.length) return renderDiff(diffContent, issue);

  return _renderDiffRows(focused, issue, language, enableHighlight, filePath);
}
