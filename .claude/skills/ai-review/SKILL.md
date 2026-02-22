# AI Review 시스템

멀티 에이전트 코드 리뷰 시스템. 리뷰 조회, 이슈 확인, 코드 수정, 검증까지 전체 라이프사이클 지원.

## Trigger

- "ai-review", "리뷰 결과", "리뷰 이슈", "dismiss", "리뷰 의견", "리뷰 리포트"
- `sessionId#issueNumber` 형식의 이슈 참조 (예: `12c32b2fb087#1`, `abc123#42`)

## 이슈 참조 형식

UI에서 `⋯` 메뉴를 통해 복사할 수 있는 이슈 참조 형식: `{sessionId}#{issueNumber}`

예: `12c32b2fb087#1`, `abc123#42`

이 형식이 입력되면 `#` 기준으로 분리하여:
1. 앞부분 → 세션 ID (`arv session list`에서 매칭)
2. 뒷부분 → 이슈 표시 번호 (해당 세션 내 이슈 순번)

조회 방법:
```bash
# 1. 세션 활성화
export ARV_BASE=http://localhost:3000/api/sessions/{sessionId}
export ARV_KEY=test
export ARV_MODEL=assistant

# 2. 이슈 목록에서 해당 번호의 이슈 찾기
arv get issues  # 목록에서 N번째 이슈 확인

# 3. 이슈 스레드 확인
arv get thread {issue_id}
```

> 표시 번호는 세션 내 이슈 생성 순서이므로, `arv get issues` 결과의 N번째 항목이 `#N`에 해당한다.

## arv CLI

`arv`는 AI Review 전용 CLI. 모든 API를 커버한다.

패키지 설치 시 `arv`가 PATH에 자동 등록된다 (`uv sync`).

서버 스코프 명령(`session create/list`, `preset list` 등)은 환경변수 없이 동작한다.

세션 스코프 명령은 환경변수가 필요하다 (세션 ID는 `arv session list`에서 확인):

```bash
export ARV_BASE=http://localhost:3000/api/sessions/{sid}
export ARV_KEY=test
export ARV_MODEL=assistant
```

### 조회 (arv get)

```bash
arv get status                       # 세션 상태
arv get index                        # 변경 파일 인덱스
arv get issues                       # 전체 이슈 목록
arv get actionable                   # 액션 필요 이슈 (fix_required, 파일별 그룹)
arv get confirmed                    # 합의 완료 이슈
arv get pending                      # 현재 모델이 검토할 미결 이슈
arv get report                       # 최종 리포트
arv get delta                        # 델타 diff (검증용)
arv get agents                       # 세션 에이전트 목록
arv get file {path} -r 10:50        # 소스 파일 읽기 (줄 범위)
arv get search {query} -g "*.py"    # 코드 검색
arv get tree {dir} -d 3             # 디렉토리 트리
arv get context {file}              # 파일별 diff 컨텍스트
arv get thread {issue_id}           # 이슈 스레드
arv get runtime {model_id}          # 에이전트 런타임 상태
arv get assist {issue_id}           # Assist 대화 이력
arv get chat {model_id}             # 에이전트 채팅 이력
arv get sessions                     # 전체 세션 목록 (서버)
arv get presets                      # 에이전트 프리셋 목록 (서버)
arv get models                       # 사용 가능한 모델 목록 (서버)
```

### 리뷰 제출

```bash
# 이슈 단건 제출
arv report -n "제목" -s high --file x.py --lines 10:20 -b "설명"
arv report -n "제목" -s high --file x.py --lines 10:20 -f /tmp/desc.md
arv report -n "제목" -s high --file x.py --lines 10:20 -b "설명" --sb "제안"

# 리뷰 완료
arv summary "전체 요약"
arv summary -f /tmp/summary.md
```

### 의견 제출

```bash
arv opinion {issue_id} -a fix_required -b "근거" -s high -c 0.9
arv opinion {issue_id} -a no_fix -b "사유"
arv opinion {issue_id} -a false_positive -b "오탐 사유"   # 원 제기자 불가
arv opinion {issue_id} -a withdraw -b "철회"               # 원 제기자만
arv opinion {issue_id} -a comment -b "코멘트"              # 투표 미포함
```

| action | 설명 |
|--------|------|
| `fix_required` | 수정 필요 |
| `no_fix` | 수정 불필요 |
| `false_positive` | 오탐 (원 제기자 불가 → 원 제기자에게 재검토 트리거) |
| `withdraw` | 철회 (원 제기자만 → 즉시 이슈 종결, `closed`) |
| `comment` | 코멘트만 (투표 미포함) |
| `raise` | 초기 이슈 제기 (Review 단계) |

### 이슈 대응 (AGENT_RESPONSE/DELIBERATING)

코딩 에이전트가 리뷰어 이슈에 응답. **FIXING 상태에서는 사용 불가.**

```bash
arv respond {issue_id} -a accept -b "동의"
arv respond {issue_id} -a dispute -b "반론 근거"   # → DELIBERATING 재진입
arv respond {issue_id} -a partial -b "일부만 반영"
```

### 이슈 상태 변경 (모든 상태에서 사용 가능)

```bash
arv status {issue_id} fixed -b "수정 내용"          # 수정 완료
arv status {issue_id} wont_fix -b "사유"             # 수정 대상 아님
arv dismiss {issue_id} -b "사유"                     # 이슈 무시
```

### 세션 제어

```bash
arv start                            # 세션 시작 (리뷰 시작)
arv finish                           # 세션 완료 (미해결 이슈 시 409)
arv finish --force                   # 강제 완료 (미해결 이슈 무시)
arv activate                         # 현재 활성 세션으로 설정
arv fix-complete -c {hash}           # 수정 완료 (전체 fix_required 대상)
arv fix-complete -c {hash} -i id1,id2  # 특정 이슈만 지정
```

### AI Assist

```bash
arv assist {issue_id} -b "이 이슈 해결 방법을 알려줘"
arv get assist {issue_id}            # 대화 이력 조회
```

### 에이전트 채팅

```bash
arv chat {model_id} -b "리뷰 진행 상황은?"
arv get chat {model_id}              # 채팅 이력 조회
```

### 구현 컨텍스트

```bash
arv impl-context -b "변경 설명" -d "결정1" -d "결정2"
arv impl-context -f /tmp/summary.md -d "결정1"
```

### 세션 관리

```bash
arv session list                     # 세션 목록
arv session create --base main --head feat --repo /path/to/repo
arv session create --base main --head feat --repo /path --auto-start
arv session create --base main --head feat --repo /path --presets p1,p2
arv session create --base main --head feat --repo /path --context "설명" --decision "결정1"
arv session delete                   # 현재 세션 삭제
```

세션 생성 전 base 브랜치 확인:
```bash
git log --oneline main..origin/main | wc -l  # origin이 앞선 커밋 수
git log --oneline origin/main..main | wc -l  # local이 앞선 커밋 수
# 둘 다 0이면 동일. 한쪽이 3+ 이상 차이나면 사용자에게 확인.
```

### 에이전트 프리셋 관리

```bash
arv preset list
arv preset add --id my-claude --client claude-code --color "#8B5CF6" --strictness strict
arv preset update {preset_id} --description "수정" --enabled false
arv preset delete {preset_id}
```

프리셋 필드: `id`, `client_type`, `provider`, `model_id`, `description`, `color`, `avatar`, `system_prompt`, `temperature`, `review_focus`, `enabled`, `strictness` (`strict`|`balanced`|`lenient`)

### 에이전트 관리 (세션 스코프)

```bash
arv agent list
arv agent add --id codex --client codex --desc "보안 리뷰"
arv agent update {model_id} --enabled false
arv agent remove {model_id}
arv get runtime {model_id}           # 런타임 상태
```

지원 클라이언트: `claude-code`, `codex`, `gemini`, `opencode`

### 연결 테스트

```bash
arv ping {callback_url}              # 기본값: ARV_KEY를 토큰으로 사용
arv ping {callback_url} --token {t} --marker {m} --client gemini
```

### 응답 형식: TOON

`X-Agent-Key` 헤더가 있는 GET 요청은 TOON(`text/toon`) 형식으로 응답. arv GET은 자동 TOON.

```
session_id: abc123
files[2,]{path,additions,deletions}:
  src/main.py,10,3
  src/util.py,5,0
```

TOON 지원 엔드포인트: status, index, context, issues, thread, pending, confirmed, delta, files, search, tree

---

## 행동 가이드 (상태별)

**항상 먼저 상태를 확인한 후, 해당 상태의 행동을 수행한다.**

```bash
arv get status
```

### REVIEWING (리뷰 진행 중)

에이전트들이 코드를 분석 중. 10분 이상 소요될 수 있다.

**대기하지 않는다.** 폴링/루프로 상태 변경을 기다리지 않는다.

1. 상태를 한 번 조회: `arv get status`
2. 사용자에게 "분석 중이므로 나중에 `/ai-review`로 확인하세요"라고 안내.
3. 여기서 종료. 상태 전이를 기다리지 않는다.

### DELIBERATING (Author 독립 판단)

에이전트 의견이 모두 나온 상태. **합의 결과와 무관하게** Author가 직접 판단한다.

1. 이슈 조회: `arv get issues`
2. 각 이슈의 스레드(에이전트 의견) 확인: `arv get thread {issue_id}`
3. **Author 독립 판단** — 합의를 참고하되, 최종 결정은 Author가 한다:
   - 수정 안함: `arv status {issue_id} wont_fix -b "사유"`
   - 수정 대상: REPORTED 상태 유지 (FIXING에서 수정)
4. `arv finish`:
   - 모든 이슈 해결됨 → **COMPLETE**
   - 미해결 이슈 있음 → 409 응답, 자동으로 **FIXING** 전환
   - 409 시 반환된 `unresolved_issues` 목록을 참고하여 FIXING 진행

### FIXING (코드 수정)

**이 단계에서 실제로 코드를 수정한다.**

1. 미해결 이슈 확인: `arv get issues` (progress_status가 reported인 이슈)
2. 각 이슈의 `file`, `line_start`, `line_end`, `description`, `suggestion`을 참고하여 **실제 코드 파일을 수정.**
3. 이슈에 동의하지 않을 때:
   - `arv status {issue_id} wont_fix -b "사유"` (수정 대상 아님으로 분류)
   - `arv dismiss {issue_id} -b "사유"` (이슈 무시)
4. 수정 완료 시: `arv status {issue_id} fixed -b "수정 내용"`
5. 커밋 후 fix-complete:
```bash
arv fix-complete -c {commit_hash}
```
6. → **VERIFYING** 자동 전환 (원 리뷰어 검증)
7. 검증 완료 후 `arv finish` → **COMPLETE**

> `arv finish --force`로 검증 없이 강제 종료 가능.

### VERIFYING (원제보자 검증)

**원제보자만** delta diff를 검증하는 단계. 자동 진행.
- 모든 이슈 해결됨 → COMPLETE
- 미해결 이슈 있음 → FIXING 재진입 (최대 2라운드)

### COMPLETE (완료)

```bash
arv get report
```

리포트 필드: `session_id`, `status`, `issues`, `issue_responses`, `fix_commits`, `verification_round`, `implementation_context`, `dismissals`, `stats`

---

## 전체 플로우 요약

```
세션 생성 (arv session create)
    ↓
세션 시작 (arv start)
    ↓
REVIEWING → DELIBERATING
    ↓
Author 독립 판단: 각 이슈를 wont_fix 또는 수정 대상으로 분류
    ↓ arv finish (미해결 이슈 → 409 + FIXING 전환)
FIXING: 코드 수정 → arv status {id} fixed → 커밋 → arv fix-complete
    ↓
VERIFYING: 원 리뷰어 검증 → COMPLETED 또는 재수정 요청
    ↓
arv finish → COMPLETE: arv get report
```

**합의 데드락 방지**: 전원 투표 완료 시 confidence 가중치 합이 threshold(기본 2)에 미달해도 다수결로 합의 결정.

**핵심: 이슈를 보고 실제로 코드를 수정하는 것이 목적. 각 상태에서 행동 후 상태를 다시 확인하여 다음 단계를 진행.**

---

## 이슈 필드 레퍼런스

`id`, `title`, `severity`, `file`, `line`, `line_start`, `line_end`, `description`, `suggestion`, `thread`, `consensus`, `consensus_type` (`fix_required`|`dismissed`|`undecided`|`closed`), `final_severity`, `raised_by`, `turn`, `assist_messages`, `progress_status` (`reported`|`wont_fix`|`fixed`|`completed`)

- `closed`: 원 제기자가 `withdraw`하여 즉시 종결된 이슈
- `turn`: 라운드 번호 (dispute 시 증가)
- `progress_status`: 이슈 해결 진행 상태 (reported → fixed → completed, 또는 reported → wont_fix)
