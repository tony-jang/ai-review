export const MODEL_COLORS = { opus:'#8B5CF6', gpt:'#22C55E', gemini:'#3B82F6', deepseek:'#F97316', human:'#58A6FF', 'human-assist':'#14B8A6' };
export const SEVERITY_COLORS = { critical:'#EF4444', high:'#F97316', medium:'#EAB308', low:'#6B7280', dismissed:'#22C55E' };
export const SEVERITY_ICONS = { critical:'🔴', high:'🟠', medium:'🟡', low:'⚪', dismissed:'✅' };
export const SEVERITY_LABELS = { critical:'심각', high:'높음', medium:'보통', low:'낮음', dismissed:'기각' };
export const ACTION_LABELS = { raise:'제기', fix_required:'수정 필요', no_fix:'수정 불필요', false_positive:'오탐', withdraw:'철회', comment:'의견', agree:'수정 필요', disagree:'수정 불필요', clarify:'의견', status_change:'상태 변경' };
export const PROGRESS_STATUS_LABELS = { reported:'보고됨', wont_fix:'수정 대상 미포함', fixed:'수정됨', completed:'완료됨' };
export const PROGRESS_STATUS_COLORS = { reported:'#3B82F6', wont_fix:'#6B7280', fixed:'#EAB308', completed:'#22C55E' };
export const STATUS_TAB_STYLES = { idle:{bg:'#6B728020',color:'#8B949E'}, collecting:{bg:'rgba(88,166,255,0.15)',color:'var(--accent)'}, reviewing:{bg:'rgba(88,166,255,0.15)',color:'var(--accent)'}, dedup:{bg:'rgba(139,92,246,0.15)',color:'var(--model-opus)'}, deliberating:{bg:'rgba(234,179,8,0.15)',color:'var(--severity-medium)'}, complete:{bg:'rgba(34,197,94,0.15)',color:'var(--severity-dismissed)'} };
export const STATUS_TAB_LABELS = { idle:'대기', collecting:'수집', reviewing:'리뷰', dedup:'중복제거', deliberating:'토론', complete:'완료' };
const _SP_CLAUDE = `You are an expert code reviewer powered by Claude. Focus on:
- Security vulnerabilities (injection, auth bypass, data exposure)
- Logic errors and edge cases that cause bugs
- Performance bottlenecks and resource leaks
- Code maintainability and adherence to project conventions
Be precise, cite line numbers, and suggest concrete fixes. Skip stylistic nitpicks unless they harm readability.`;

const _SP_CODEX = `You are an expert code reviewer powered by GPT Codex. Focus on:
- Security vulnerabilities (injection, auth bypass, data exposure)
- Logic errors and edge cases that cause bugs
- Performance bottlenecks and resource leaks
- Code maintainability and adherence to project conventions
Be precise, cite line numbers, and suggest concrete fixes. Skip stylistic nitpicks unless they harm readability.`;

const _SP_GEMINI = `You are an expert code reviewer powered by Gemini. Focus on:
- Security vulnerabilities (injection, auth bypass, data exposure)
- Logic errors and edge cases that cause bugs
- Performance bottlenecks and resource leaks
- Code maintainability and adherence to project conventions
Be precise, cite line numbers, and suggest concrete fixes. Skip stylistic nitpicks unless they harm readability.`;

const _SP_OPENCODE = `You are an expert code reviewer. Focus on:
- Security vulnerabilities (injection, auth bypass, data exposure)
- Logic errors and edge cases that cause bugs
- Performance bottlenecks and resource leaks
- Code maintainability and adherence to project conventions
Be precise, cite line numbers, and suggest concrete fixes. Skip stylistic nitpicks unless they harm readability.`;

// Provider SVG icon paths (Simple Icons, fill="currentColor" compatible)
export const PROVIDER_ICONS = {
  anthropic: { viewBox:'0 0 24 24', path:'M17.3041 3.541h-3.6718l6.696 16.918H24Zm-10.6082 0L0 20.459h3.7442l1.3693-3.5527h7.0052l1.3693 3.5528h3.7442L10.5363 3.5409Zm-.3712 10.2232 2.2914-5.9456 2.2914 5.9456Z' },
  openai:    { viewBox:'0 0 24 24', path:'M22.2819 9.8211a5.9847 5.9847 0 0 0-.5157-4.9108 6.0462 6.0462 0 0 0-6.5098-2.9A6.0651 6.0651 0 0 0 4.9807 4.1818a5.9847 5.9847 0 0 0-3.9977 2.9 6.0462 6.0462 0 0 0 .7427 7.0966 5.98 5.98 0 0 0 .511 4.9107 6.051 6.051 0 0 0 6.5146 2.9001A5.9847 5.9847 0 0 0 13.2599 24a6.0557 6.0557 0 0 0 5.7718-4.2058 5.9894 5.9894 0 0 0 3.9977-2.9001 6.0557 6.0557 0 0 0-.7475-7.0729zm-9.022 12.6081a4.4755 4.4755 0 0 1-2.8764-1.0408l.1419-.0804 4.7783-2.7582a.7948.7948 0 0 0 .3927-.6813v-6.7369l2.02 1.1686a.071.071 0 0 1 .038.052v5.5826a4.504 4.504 0 0 1-4.4945 4.4944zm-9.6607-4.1254a4.4708 4.4708 0 0 1-.5346-3.0137l.142.0852 4.783 2.7582a.7712.7712 0 0 0 .7806 0l5.8428-3.3685v2.3324a.0804.0804 0 0 1-.0332.0615L9.74 19.9502a4.4992 4.4992 0 0 1-6.1408-1.6464zM2.3408 7.8956a4.485 4.485 0 0 1 2.3655-1.9728V11.6a.7664.7664 0 0 0 .3879.6765l5.8144 3.3543-2.0201 1.1685a.0757.0757 0 0 1-.071 0l-4.8303-2.7865A4.504 4.504 0 0 1 2.3408 7.872zm16.5963 3.8558L13.1038 8.364l2.0154-1.164a.0757.0757 0 0 1 .071 0l4.8303 2.7913a4.4944 4.4944 0 0 1-.6765 8.1042v-5.6772a.79.79 0 0 0-.407-.667zm2.0107-3.0231l-.142-.0852-4.7735-2.7818a.7759.7759 0 0 0-.7854 0L9.409 9.2297V6.8974a.0662.0662 0 0 1 .0284-.0615l4.8303-2.7866a4.4992 4.4992 0 0 1 6.6802 4.66zM8.3065 12.863l-2.02-1.1638a.0804.0804 0 0 1-.038-.0567V6.0742a4.4992 4.4992 0 0 1 7.3757-3.4537l-.142.0805L8.704 5.459a.7948.7948 0 0 0-.3927.6813zm1.0976-2.3654l2.602-1.4998 2.6069 1.4998v2.9994l-2.5974 1.4997-2.6067-1.4997Z' },
  gemini:    { viewBox:'0 0 24 24', path:'M11.04 19.32Q12 21.51 12 24q0-2.49.93-4.68.96-2.19 2.58-3.81t3.81-2.55Q21.51 12 24 12q-2.49 0-4.68-.93a12.3 12.3 0 0 1-3.81-2.58 12.3 12.3 0 0 1-2.58-3.81Q12 2.49 12 0q0 2.49-.96 4.68-.93 2.19-2.55 3.81a12.3 12.3 0 0 1-3.81 2.58Q2.49 12 0 12q2.49 0 4.68.96 2.19.93 3.81 2.55t2.55 3.81' },
  opencode:  { viewBox:'0 0 24 24', fillRule:'evenodd', path:'M18 20H6V8H18V20ZM18 4H6V20H18V4ZM24 24H0V0H24V24Z' },
};

function _providerSvg(key, size) {
  const ic = PROVIDER_ICONS[key];
  if (!ic) return '';
  const s = size || 16;
  const fr = ic.fillRule ? ` fill-rule="${ic.fillRule}"` : '';
  return `<svg viewBox="${ic.viewBox}" width="${s}" height="${s}" fill="currentColor" class="provider-icon"><path d="${ic.path}"${fr}/></svg>`;
}

// Map client_type → icon key
const _CLIENT_ICON_KEY = { 'claude-code':'anthropic', codex:'openai', gemini:'gemini', opencode:'opencode' };
export function providerIconSvg(clientType, size) { return _providerSvg(_CLIENT_ICON_KEY[clientType] || 'opencode', size); }

export const AGENT_PRESETS = [
  // Claude Code
  { group:'Claude Code', id:'claude-code-opus',   label:'Opus 4.6',       client_type:'claude-code', model_id:'claude-opus-4-6',   color:'#8B5CF6', system_prompt:_SP_CLAUDE },
  { group:'Claude Code', id:'claude-code-sonnet',  label:'Sonnet 4.6',     client_type:'claude-code', model_id:'claude-sonnet-4-6', color:'#A78BFA', system_prompt:_SP_CLAUDE },
  { group:'Claude Code', id:'claude-code-haiku',   label:'Haiku 4.5',      client_type:'claude-code', model_id:'claude-haiku-4-5',  color:'#C4B5FD', system_prompt:_SP_CLAUDE },
  // Codex (OpenAI)
  { group:'Codex',       id:'codex-gpt53',         label:'GPT-5.3 Codex',    client_type:'codex', model_id:'gpt-5.3-codex',    color:'#22C55E', system_prompt:_SP_CODEX },
  { group:'Codex',       id:'codex-gpt52',         label:'GPT-5.2 Codex',    client_type:'codex', model_id:'gpt-5.2-codex',    color:'#4ADE80', system_prompt:_SP_CODEX },
  { group:'Codex',       id:'codex-mini',          label:'GPT-5 Codex Mini', client_type:'codex', model_id:'gpt-5-codex-mini', color:'#86EFAC', system_prompt:_SP_CODEX },
  // Gemini (Google)
  { group:'Gemini',      id:'gemini-31-pro',       label:'Gemini 3.1 Pro',  client_type:'gemini', model_id:'gemini-3.1-pro-preview', color:'#3B82F6', system_prompt:_SP_GEMINI },
  { group:'Gemini',      id:'gemini-25-pro',       label:'Gemini 2.5 Pro',  client_type:'gemini', model_id:'gemini-2.5-pro',         color:'#60A5FA', system_prompt:_SP_GEMINI },
  { group:'Gemini',      id:'gemini-25-flash',     label:'Gemini 2.5 Flash', client_type:'gemini', model_id:'gemini-2.5-flash',      color:'#93C5FD', system_prompt:_SP_GEMINI },
  // OpenCode (무료)
  { group:'OpenCode',    id:'opencode-big-pickle',  label:'Big Pickle Free',      client_type:'opencode', provider:'opencode', model_id:'big-pickle',                  color:'#F97316', system_prompt:_SP_OPENCODE },
  { group:'OpenCode',    id:'opencode-minimax',     label:'MiniMax M2.5 Free',    client_type:'opencode', provider:'opencode', model_id:'minimax-m2.5-free',            color:'#EF4444', system_prompt:_SP_OPENCODE },
  { group:'OpenCode',    id:'opencode-glm',         label:'GLM-5 Free',           client_type:'opencode', provider:'opencode', model_id:'glm-5-free',                   color:'#22D3EE', system_prompt:_SP_OPENCODE },
  { group:'OpenCode',    id:'opencode-gpt5-nano',   label:'GPT-5 Nano Free',      client_type:'opencode', provider:'opencode', model_id:'gpt-5-nano',                   color:'#FB923C', system_prompt:_SP_OPENCODE },
  { group:'OpenCode',    id:'opencode-trinity',     label:'Trinity Large Free',   client_type:'opencode', provider:'opencode', model_id:'trinity-large-preview-free',   color:'#A78BFA', system_prompt:_SP_OPENCODE },
];


export const STRICTNESS_OPTIONS = [
  { value:'strict',   label:'엄격', desc:'사소한 것도 빠짐없이 지적' },
  { value:'balanced', label:'균형', desc:'실질적 영향 있는 이슈 위주' },
  { value:'lenient',  label:'관대', desc:'심각한 버그/보안만 지적' },
];

export const AGENT_FIELD_HELP = {
  description: '에이전트 카드에 표시되는 한 줄 설명입니다.',
  strictness: '엄격도는 지적 범위를 조절합니다. 엄격할수록 사소한 항목도 더 많이 검토합니다.',
  client_type: '실행할 에이전트 클라이언트 종류입니다.',
  model_id: '프리셋은 일부 예시입니다. 비우면 각 CLI/서버 기본 모델을 사용하며, 원하는 모델 ID를 직접 입력할 수 있습니다.',
  provider: 'OpenCode에서 사용할 모델 provider ID입니다.',
  system_prompt: '리뷰 스타일/제약을 강하게 주고 싶을 때 입력하세요.',
  temperature: '0에 가까울수록 일관적이고, 높을수록 다양하게 응답합니다. 비우면 클라이언트 기본값을 사용합니다.',
  review_focus: '쉼표로 나눠 핵심 점검 항목을 지정합니다.',
  test_endpoint: '테스트 시 콜백 URL/세션 ID를 자동 생성해 LLM에게 전달합니다.',
};

export const MODEL_DEFAULT_HINTS = {
  'claude-code': '기본값(빈 값): Claude CLI 기본 모델 사용',
  'codex': '기본값(빈 값): Codex CLI 기본 모델 사용',
  'gemini': '기본값(빈 값): Gemini CLI 기본 모델 사용',
  'opencode': '기본값(빈 값): OpenCode 서버 기본 모델 사용',
};

export const ACTIVITY_LABELS = {
  view_file: '파일 확인',
  search: '검색',
  view_tree: '구조 탐색',
  view_diff: 'diff 확인',
  view_context: '컨텍스트 확인',
  view_index: '인덱스 확인',
  Read: '파일 읽기',
  Grep: '패턴 검색',
  Glob: '파일 탐색',
  Bash: '명령 실행',
  arv_get_file: '파일 조회',
  arv_get_index: '인덱스 조회',
  arv_get_search: '코드 검색',
  arv_get_tree: '구조 조회',
  arv_get_context: '컨텍스트 조회',
  arv_get_thread: '스레드 조회',
  arv_get_delta: '델타 조회',
  arv_get_confirmed: '확정 이슈 조회',
  arv_report: '이슈 제출',
  arv_summary: '리뷰 완료',
  arv_opinion: '의견 제출',
  arv_finish: '리뷰 종료',
  arv_respond: '이슈 응답',
  arv_dismiss: '이슈 기각',
  arv_status: '상태 변경',
  arv_fix_complete: '수정 완료',
  arv_get_status: '상태 조회',
  arv_get_issues: '이슈 목록 조회',
  arv_get_pending: '대기 이슈 조회',
  arv_get_actionable: '조치 이슈 조회',
  arv_get_report: '리포트 조회',
  arv_ping: '연결 테스트',
  arv_assist: 'AI 도움 요청',
  arv_impl_context: '구현 컨텍스트 제출',
  arv_start: '세션 시작',
  arv_activate: '세션 활성화',
};

export const ACTIVITY_STALE_MS = 30000;
export const MAX_ACTIVITY_HISTORY = 50;
