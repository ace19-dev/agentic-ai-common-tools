# Examples

공통 Tool과 MCP를 조합하여 실제 시나리오를 처리하는 예시 모음입니다.  
모든 예시는 동일한 LangGraph `Planner → Executor → Reviewer` 워크플로우 위에서 동작합니다 (flight_monitor 제외).

---

## 예시 목록

| 예시 | 파일 | 사용 Tool |
|------|------|-----------|
| [Customer Support](#1-customer_supportpy) | `customer_support.py` | retrieval, memory, notify |
| [Research Agent](#2-research_agentpy) | `research_agent.py` | http, retrieval, memory, notify |
| [Monitoring Agent](#3-monitoring_agentpy) | `monitoring_agent.py` | http, memory, notify, scheduler |
| [Flight Monitor](#4-flight_monitor) | `flight_monitor/` | flight, memory, notify (4-에이전트) |

---

## 1. `customer_support.py`

FAQ 문서를 TF-IDF 인덱스에 적재하고, 사용자 질문과 유사도를 비교하여 답변하거나 Slack으로 에스컬레이션합니다.

**단계별 흐름:**
```
사용자 질문
    ↓
retrieval_index   ← FAQ 6개 적재 (idempotent upsert)
    ↓
retrieval_search  ← 코사인 유사도 기반 top-5 검색
    ↓
memory_set        ← 질문을 'support' 네임스페이스에 저장
    ↓
score ≥ 0.1 → 답변 반환
score < 0.1 → notify_slack → #support-escalation
```

**실행:**
```bash
python -m examples.customer_support
python -m examples.customer_support "환불 정책이 어떻게 되나요?"
python main.py --example customer_support "비밀번호를 잊어버렸어요"
```

---

## 2. `research_agent.py`

지정된 URL에서 콘텐츠를 수집하고 인덱싱한 뒤, 연구 주제로 검색하여 요약 보고서를 이메일로 발송합니다.

**단계별 흐름:**
```
연구 주제 + URL 목록
    ↓
http_get          ← 각 URL 콘텐츠 수집 (자동 retry, 10,000자 truncate)
    ↓
retrieval_index   ← URL을 doc_id로 동적 인덱싱
    ↓
retrieval_search  ← 주제 관련 문서 검색 (top_k=5)
    ↓
요약 생성 (3~5문장, 출처 명시)
    ↓
memory_set        ← 요약을 'research' 네임스페이스에 저장
    ↓
notify_email      ← 연구 보고서 발송 (NOTIFICATION_DRY_RUN=true 시 콘솔 출력)
```

**실행:**
```bash
python -m examples.research_agent
python -m examples.research_agent "Python async patterns"
EMAIL_RECIPIENT=you@email.com python -m examples.research_agent
python main.py --example research "LangGraph multi-agent"
```

---

## 3. `monitoring_agent.py`

대상 URL 목록을 헬스체크하고, 비정상 응답에 대해 Slack 즉시 알림 + 요약 리포트를 발송합니다.  
선택적으로 APScheduler에 반복 실행 job을 등록합니다.

**단계별 흐름:**
```
대상 URL 목록
    ↓
http_get          ← 각 URL 헬스체크 (HTTP status 확인)
    ↓
memory_set        ← 결과를 'monitoring' 네임스페이스에 저장
    ↓
status ≠ 200 → notify_slack → #alerts (타겟별 개별 알림)
    ↓
notify_slack      ← #monitoring 헬스체크 요약 (X/Y 정상)
    ↓
notify_console    ← 실행 완료 로그
```

**실행:**
```bash
python -m examples.monitoring_agent
python -m examples.monitoring_agent https://httpbin.org/status/200 https://httpbin.org/status/503
MONITORING_TARGETS=https://api.example.com/health python -m examples.monitoring_agent
python main.py --example monitoring
```

**환경 변수:**
- `MONITORING_TARGETS` — 쉼표 구분 URL 목록 (코드 수정 없이 대상 변경)
- `NOTIFICATION_DRY_RUN=true` — Slack 발송 없이 콘솔 출력 (기본값)

---

## 4. `flight_monitor/`

값싼 항공권이 나타날 때까지 주기적으로 가격을 모니터링하다가 임계값 이하 가격이 감지되면 자동 예약 + Slack/이메일 알림을 전송하는 **4-에이전트 시스템**입니다.

범용 워크플로우가 아닌 **도메인 특화 LangGraph**로 구현되어 있으며, 각 에이전트는 최소 권한 원칙에 따라 필요한 tool만 접근합니다.

자세한 내용은 [`flight_monitor/README.md`](flight_monitor/README.md)를 참조하세요.

**빠른 실행:**
```bash
python -m examples.flight_monitor.run
python -m examples.flight_monitor.run --origin ICN --dest BKK --max-price 350
```

---

## 공통 환경 변수

```bash
# .env 또는 셸 환경에 설정
OPENAI_API_KEY=sk-...          # 필수
NOTIFICATION_DRY_RUN=true      # 기본값 — 실제 Slack/이메일 발송 없이 콘솔 출력
EMAIL_RECIPIENT=you@email.com  # 이메일 수신자 (research_agent, flight_monitor)
```

`.env.example`을 복사하여 시작하세요:
```bash
cp .env.example .env
```
