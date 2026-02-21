export const MODEL_COLORS = { opus:'#8B5CF6', gpt:'#22C55E', gemini:'#3B82F6', deepseek:'#F97316', human:'#58A6FF', 'human-assist':'#14B8A6' };
export const SEVERITY_COLORS = { critical:'#EF4444', high:'#F97316', medium:'#EAB308', low:'#6B7280', dismissed:'#22C55E' };
export const SEVERITY_ICONS = { critical:'🔴', high:'🟠', medium:'🟡', low:'⚪', dismissed:'✅' };
export const SEVERITY_LABELS = { critical:'심각', high:'높음', medium:'보통', low:'낮음', dismissed:'기각' };
export const ACTION_LABELS = { raise:'제기', fix_required:'수정 필요', no_fix:'수정 불필요', false_positive:'오탐', withdraw:'철회', comment:'의견', agree:'수정 필요', disagree:'수정 불필요', clarify:'의견' };
export const STATUS_TAB_STYLES = { idle:{bg:'#6B728020',color:'#8B949E'}, collecting:{bg:'rgba(88,166,255,0.15)',color:'var(--accent)'}, reviewing:{bg:'rgba(88,166,255,0.15)',color:'var(--accent)'}, dedup:{bg:'rgba(139,92,246,0.15)',color:'var(--model-opus)'}, deliberating:{bg:'rgba(234,179,8,0.15)',color:'var(--severity-medium)'}, complete:{bg:'rgba(34,197,94,0.15)',color:'var(--severity-dismissed)'} };
export const STATUS_TAB_LABELS = { idle:'대기', collecting:'수집', reviewing:'리뷰', dedup:'중복제거', deliberating:'토론', complete:'완료' };
export const AGENT_PRESETS = [
  { id:'claude-code', label:'Claude Code', client_type:'claude-code', color:'#8B5CF6', icon:'🟣' },
  { id:'codex', label:'Codex', client_type:'codex', color:'#22C55E', icon:'🟢' },
  { id:'gemini', label:'Gemini', client_type:'gemini', color:'#3B82F6', icon:'🔵' },
  { id:'opencode', label:'OpenCode', client_type:'opencode', color:'#F97316', icon:'🟠', needsProvider:true },
];

export const REVIEW_PRESETS = [
  { key:'engineering', label:'엔지니어링', icon:'🔧',
    review_focus:['code quality','error handling','performance','maintainability'],
    system_prompt:'You are a senior software engineer focused on code quality, error handling, performance, and maintainability.' },
  { key:'security', label:'시큐리티', icon:'🛡️',
    review_focus:['injection','authentication','authorization','data exposure','cryptography'],
    system_prompt:'You are a security specialist focused on OWASP Top 10, injection flaws, auth issues, and data exposure.' },
  { key:'architecture', label:'아키텍처', icon:'🏗️',
    review_focus:['design patterns','coupling','cohesion','scalability','API design'],
    system_prompt:'You are a software architect focused on design patterns, coupling, cohesion, scalability, and API design.' },
  { key:'testing', label:'테스팅', icon:'🧪',
    review_focus:['test coverage','edge cases','test design','assertions','mocking'],
    system_prompt:'You are a test quality specialist focused on coverage, edge cases, test design, assertions, and mocking strategy.' },
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
};

export const ACTIVITY_STALE_MS = 30000;
export const MAX_ACTIVITY_HISTORY = 50;
