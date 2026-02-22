# AI Review

멀티 에이전트 코드 리뷰 오케스트레이터.

## 설치

```bash
# 개발 환경 (프로젝트 내에서만 사용)
uv sync

# 글로벌 CLI 설치 (arv, ai-review 명령을 어디서든 사용)
uv tool install -e .

# 글로벌 CLI 제거
uv tool uninstall ai-review
```

## 실행

```bash
# 서버 시작 (기본 포트 3000)
ai-review

# 포트 지정
ai-review --port 8080
```

## arv CLI

`arv`는 AI Review REST API를 래핑한 bash CLI.

- 서버 스코프 명령(`arv session create/list`, `arv preset list` 등)은 환경변수 없이 동작 (기본: localhost:3000)
- 세션 스코프 명령은 `ARV_BASE`, `ARV_KEY`, `ARV_MODEL` 환경변수 필요 (에이전트 실행 시 자동 주입)
- `ARV_HOST` 환경변수로 서버 주소 변경 가능 (기본: `http://localhost:3000`)

## 테스트

```bash
uv run pytest tests/ -x -q
```
