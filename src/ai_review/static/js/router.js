import state from './state.js';

export const router = {
  _ID_RE: /^[0-9a-f]{12}$/,
  parse(url) {
    const path = (url ? new URL(url, location.origin).pathname : location.pathname).replace(/\/+$/, '') || '/';
    const hash = url ? new URL(url, location.origin).hash : location.hash;
    const parts = path.split('/').filter(Boolean);
    const result = { sessionId: null, issueId: null, mainTab: 'conversation', opinionId: null };
    if (parts.length >= 1 && this._ID_RE.test(parts[0])) {
      result.sessionId = parts[0];
      if (parts.length >= 2 && parts[1] === 'changes') {
        result.mainTab = 'changes';
      }
      if (parts.length >= 3 && parts[1] === 'issues' && this._ID_RE.test(parts[2])) {
        result.issueId = parts[2];
        result.mainTab = 'changes';
      }
    }
    if (hash && hash.startsWith('#op-')) {
      result.opinionId = hash.slice(4);
    }
    return result;
  },
  buildUrl(opts) {
    const sid = opts.sessionId || state.sessionId;
    if (!sid) return '/';
    let path = `/${sid}`;
    if (opts.issueId) {
      path += `/issues/${opts.issueId}`;
    } else if (opts.mainTab === 'changes') {
      path += '/changes';
    }
    if (opts.opinionId) path += `#op-${opts.opinionId}`;
    return path;
  },
  push(opts) {
    const url = this.buildUrl(opts);
    if (url !== location.pathname + location.hash) {
      history.pushState(null, '', url);
    }
  },
  replace(opts) {
    const url = this.buildUrl(opts);
    if (url !== location.pathname + location.hash) {
      history.replaceState(null, '', url);
    }
  },
  sync() {
    const opts = { sessionId: state.sessionId, mainTab: state.mainTab };
    this.replace(opts);
  }
};

export function _scrollToOpinion(opinionId) {
  const el = document.getElementById('op-' + opinionId);
  if (!el) return;
  el.scrollIntoView({ behavior: 'smooth', block: 'center' });
  el.classList.add('opinion-flash');
  setTimeout(() => el.classList.remove('opinion-flash'), 1200);
}

export function initRouter() {
  window.addEventListener('popstate', async () => {
    const parsed = router.parse();
    if (!parsed.sessionId) {
      // URL이 / 이면 현재 세션 유지
      return;
    }
    const sessionExists = state.sessions.some(s => s.session_id === parsed.sessionId);
    if (!sessionExists) {
      router.replace({ sessionId: state.sessionId, mainTab: state.mainTab });
      return;
    }
    if (parsed.sessionId !== state.sessionId) {
      await window.switchSession(parsed.sessionId, { push: false });
    }
    if (parsed.issueId) {
      const issueExists = state.issues.some(i => i.id === parsed.issueId);
      if (issueExists) {
        window.selectIssue(parsed.issueId, { push: false });
      } else if (parsed.mainTab !== state.mainTab) {
        window.switchMainTab(parsed.mainTab, { push: false });
      }
    } else {
      if (state.selectedIssue) state.selectedIssue = null;
      if (parsed.mainTab !== state.mainTab) {
        window.switchMainTab(parsed.mainTab, { push: false });
      }
    }
    if (parsed.opinionId) {
      setTimeout(() => _scrollToOpinion(parsed.opinionId), 100);
    }
  });
}
