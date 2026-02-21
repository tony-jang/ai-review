import { esc, _escapeRegex } from '../utils.js';

// Module-local mutable state
let _highlighterLoadPromise = null;
let _highlighterUnavailable = false;
let _starryNightRuntime = null;
let _starryScopeSet = null;

function _hastToHtml(node) {
  if (!node) return '';
  if (node.type === 'text') return esc(node.value || '');
  const children = Array.isArray(node.children) ? node.children.map(_hastToHtml).join('') : '';
  if (node.type === 'root') return children;
  if (node.type !== 'element') return children;
  const tagName = String(node.tagName || 'span').toLowerCase();
  // Starry Night returns spans for token wrappers; keep only safe inline tags here.
  if (tagName !== 'span') return children;
  const props = node.properties || {};
  let className = '';
  if (Array.isArray(props.className)) className = props.className.filter(Boolean).join(' ');
  else if (typeof props.className === 'string') className = props.className;
  const classAttr = className ? ` class="${_escapeAttr(className)}"` : '';
  return `<span${classAttr}>${children}</span>`;
}

function _escapeAttr(s) {
  return String(s || '')
    .replace(/&/g, '&amp;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

function _starryLanguageCandidates(language) {
  const lang = (language || '').toLowerCase();
  const map = {
    javascript: ['source.js', 'javascript', 'js'],
    typescript: ['source.ts', 'typescript', 'ts'],
    python: ['source.python', 'python', 'py'],
    go: ['source.go', 'go'],
    java: ['source.java', 'java'],
    kotlin: ['source.kotlin', 'kotlin', 'kt', 'kts'],
    csharp: ['source.cs', 'csharp', 'cs'],
    c: ['source.c', 'c'],
    cpp: ['source.cpp', 'cpp', 'c++'],
    rust: ['source.rust', 'rust', 'rs'],
    ruby: ['source.ruby', 'ruby', 'rb'],
    php: ['source.php', 'php'],
    swift: ['source.swift', 'swift'],
    scala: ['source.scala', 'scala'],
    sql: ['source.sql', 'sql'],
    bash: ['source.shell', 'shell', 'bash', 'sh'],
    yaml: ['source.yaml', 'yaml', 'yml'],
    json: ['source.json', 'json'],
    xml: ['source.xml', 'xml'],
    html: ['source.html', 'html'],
    css: ['source.css', 'css'],
    markdown: ['source.gfm', 'markdown', 'md'],
  };
  return map[lang] || [lang];
}

export function _starryScopeForLanguage(language) {
  if (!_starryNightRuntime || !language) return '';
  const candidates = _starryLanguageCandidates(language);
  for (const c of candidates) {
    if (!c) continue;
    if (_starryScopeSet && _starryScopeSet.has(c)) return c;
    if (_starryNightRuntime.flagToScope) {
      const scope = _starryNightRuntime.flagToScope(c);
      if (scope) return scope;
    }
  }
  return '';
}

export function _ensureDiffHighlighter() {
  if (_starryNightRuntime) return Promise.resolve(true);
  if (_highlighterUnavailable) return Promise.resolve(false);
  if (_highlighterLoadPromise) return _highlighterLoadPromise;
  _highlighterLoadPromise = (async () => {
    try {
      const mod = await import('/vendor/starry-night.bundle.mjs');
      const createStarryNight = mod?.createStarryNight;
      const grammars = mod?.all || mod?.common;
      if (typeof createStarryNight !== 'function' || !Array.isArray(grammars)) {
        throw new Error('starry-night module is invalid');
      }
      _starryNightRuntime = await createStarryNight(grammars, {
        getOnigurumaUrlFetch() {
          return new URL('/vendor/onig.wasm', window.location.origin);
        },
      });
      _starryScopeSet = new Set(_starryNightRuntime.scopes ? _starryNightRuntime.scopes() : []);
      return true;
    } catch (e) {
      _highlighterUnavailable = true;
      return false;
    }
  })();
  return _highlighterLoadPromise;
}

let _refreshCallbackAttached = false;

export function _requestDiffHighlighterRefresh() {
  if (_starryNightRuntime || _highlighterUnavailable) return;
  if (_refreshCallbackAttached) return;
  _refreshCallbackAttached = true;
  _ensureDiffHighlighter().then((ok) => {
    _refreshCallbackAttached = false;
    if (!ok) return;
    window.renderMainTabContent();
  });
}

export function _guessDiffLanguage(filePath) {
  const path = (filePath || '').toLowerCase();
  const name = path.split('/').pop() || '';
  if (name === 'dockerfile') return 'dockerfile';
  if (name === 'makefile' || name.startsWith('makefile.')) return 'makefile';
  const ext = name.includes('.') ? name.split('.').pop() : '';
  const byExt = {
    js: 'javascript',
    mjs: 'javascript',
    cjs: 'javascript',
    jsx: 'javascript',
    ts: 'typescript',
    tsx: 'typescript',
    py: 'python',
    go: 'go',
    java: 'java',
    kt: 'kotlin',
    kts: 'kotlin',
    cs: 'csharp',
    c: 'c',
    h: 'cpp',
    hpp: 'cpp',
    cpp: 'cpp',
    cc: 'cpp',
    cxx: 'cpp',
    rs: 'rust',
    rb: 'ruby',
    php: 'php',
    swift: 'swift',
    scala: 'scala',
    sql: 'sql',
    sh: 'bash',
    bash: 'bash',
    yml: 'yaml',
    yaml: 'yaml',
    json: 'json',
    xml: 'xml',
    html: 'html',
    css: 'css',
    md: 'markdown',
  };
  return byExt[ext] || '';
}

function _basicKeywordsForLanguage(language) {
  const lang = (language || '').toLowerCase();
  if (lang === 'python') return ['def','class','import','from','as','if','elif','else','for','while','try','except','finally','with','return','yield','lambda','pass','break','continue','and','or','not','in','is','None','True','False','async','await','raise'];
  if (lang === 'go') return ['package','import','func','type','struct','interface','map','chan','select','go','defer','return','if','else','switch','case','default','for','range','break','continue','fallthrough','const','var','nil'];
  if (lang === 'java') return ['package','import','class','interface','enum','extends','implements','public','private','protected','static','final','void','new','return','if','else','switch','case','default','for','while','do','try','catch','finally','throw','throws','this','super','null','true','false'];
  if (lang === 'kotlin') return ['package','import','class','interface','object','fun','val','var','typealias','public','private','protected','internal','open','final','override','abstract','sealed','data','companion','operator','infix','inline','suspend','tailrec','const','lateinit','init','by','where','when','if','else','for','while','do','try','catch','finally','return','break','continue','this','super','is','in','as','null','true','false'];
  if (lang === 'csharp') return ['namespace','using','class','struct','interface','enum','public','private','protected','internal','static','readonly','const','void','new','return','if','else','switch','case','default','for','foreach','while','do','try','catch','finally','throw','this','base','null','true','false','async','await','var'];
  if (lang === 'rust') return ['fn','let','mut','struct','enum','impl','trait','pub','use','mod','crate','self','super','match','if','else','loop','while','for','in','return','break','continue','async','await','move','ref','const','static','true','false','None','Some'];
  if (lang === 'bash' || lang === 'shell') return ['if','then','else','elif','fi','for','while','do','done','case','esac','function','in','export','local','readonly','unset','return','break','continue'];
  if (lang === 'json' || lang === 'yaml' || lang === 'xml' || lang === 'ini') return ['true','false','null','yes','no','on','off'];
  // javascript/typescript/c-family fallback
  return ['function','class','interface','type','enum','import','export','from','as','const','let','var','new','return','if','else','switch','case','default','for','while','do','try','catch','finally','throw','extends','implements','public','private','protected','static','async','await','yield','break','continue','this','super','null','true','false','typeof','instanceof'];
}

function _basicHighlightDiffCode(text, language) {
  let src = text || '';
  const lang = (language || '').toLowerCase();
  const stash = [];
  const save = (cls, val) => {
    const token = `@@BT_${stash.length}@@`;
    stash.push(`<span class="${cls}">${esc(val)}</span>`);
    return token;
  };

  // Strings first to keep markers inside strings from being tokenized as comments/keywords.
  src = src.replace(/`[^`]*`/g, (m) => save('diff-tok-string', m));
  src = src.replace(/"(?:\\.|[^"\\])*"/g, (m) => save('diff-tok-string', m));
  src = src.replace(/'(?:\\.|[^'\\])*'/g, (m) => save('diff-tok-string', m));

  const hashCommentLang = new Set(['python', 'bash', 'shell', 'yaml', 'ini', 'toml']);
  if (hashCommentLang.has(lang)) {
    src = src.replace(/#.*$/g, (m) => save('diff-tok-comment', m));
  } else {
    src = src.replace(/\/\/.*$/g, (m) => save('diff-tok-comment', m));
  }
  src = src.replace(/\/\*[\s\S]*?\*\//g, (m) => save('diff-tok-comment', m));

  const keywords = _basicKeywordsForLanguage(lang);
  for (const kw of keywords) {
    const re = new RegExp(`\\b${_escapeRegex(kw)}\\b`, 'g');
    src = src.replace(re, (m) => save('diff-tok-keyword', m));
  }

  src = esc(src);
  stash.forEach((html, i) => {
    src = src.replace(`@@BT_${i}@@`, html);
  });
  return src;
}

export function _highlightDiffCode(text, language, enableHighlight = true) {
  const raw = text || '';
  if (!enableHighlight || !language) return esc(raw);
  if (!_starryNightRuntime) {
    _requestDiffHighlighterRefresh();
    return _basicHighlightDiffCode(raw, language);
  }
  try {
    const scope = _starryScopeForLanguage(language);
    if (!scope) return _basicHighlightDiffCode(raw, language);
    const tree = _starryNightRuntime.highlight(raw, scope);
    const html = _hastToHtml(tree);
    return html || _basicHighlightDiffCode(raw, language);
  } catch (e) {
    return _basicHighlightDiffCode(raw, language);
  }
}
