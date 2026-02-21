import state from './state.js';

export async function fetchDiff(filePath) {
  if (state.diffCache[filePath]) return state.diffCache[filePath];
  if (!state.sessionId) return '';
  try {
    const r = await fetch(`/api/sessions/${state.sessionId}/diff/${filePath}`);
    if (!r.ok) return '';
    const d = await r.json();
    state.diffCache[filePath] = d.content;
    return d.content;
  } catch(e) { return ''; }
}

export async function fetchFileLines(filePath, start, end) {
  if (!state.sessionId) return null;
  try {
    const r = await fetch(`/api/sessions/${state.sessionId}/files/${filePath}?start=${start}&end=${end}`);
    if (!r.ok) return null;
    return await r.json();
  } catch(e) { return null; }
}

export async function fetchSessions() {
  try {
    const r = await fetch('/api/sessions');
    if (r.ok) state.sessions = await r.json();
  } catch (e) { /* ignore */ }
}
