# Flight Monitor

값싼 항공권이 나타날 때까지 주기적으로 가격을 모니터링하다가, 임계값 이하 가격이 감지되면 **자동 예약 + Slack/이메일 알림**을 전송하는 4-에이전트 시스템입니다.

![Flight Monitor Workflow](../../assets/flight_monitor_workflow.svg)

---

## 아키텍처

범용 `Planner → Executor → Reviewer` 워크플로우 대신, 이 예시는 **도메인 특화 LangGraph**를 직접 구성합니다.  
4개의 전문화된 에이전트가 하나의 공유 `ToolNode`를 통해 tool을 실행하며, `FlightState.active_phase` 필드로 어느 에이전트로 복귀할지 결정합니다.

### 4-Agent 구성 (Principle of Least Privilege)

| 에이전트 | 파일 | 역할 | 전용 Tool |
|----------|------|------|-----------|
| `SearchAgent` | `agents.py` | 항공권 검색 API 호출, 결과를 memory에 저장 | `http_get`, `memory_set`, `notify_console` |
| `PriceAnalysisAgent` | `agents.py` | 가격 vs 임계값 비교, 예약 여부 결정 | `memory_get` + Pydantic Structured Output |
| `BookingAgent` | `agents.py` | 예약 API 호출, 암호화 API 키 사용, 확인서 저장 | `http_post`, `auth_get_key`, `memory_set` |
| `NotificationAgent` | `agents.py` | Slack + 이메일 예약 확인 알림 발송 | `notify_slack`, `notify_email`, `memory_set` |

### 그래프 토폴로지 (1 사이클 = 1 모니터링 체크)

```
START
  │
  ▼
search ──[tool_calls?]──► tools ──► search
  │
  ▼ (done)
price_analysis                     (Structured Output — tool call 없음)
  │
  ├─[should_book=True]──► booking ──[tool_calls?]──► tools ──► booking
  │                           │
  │                           ▼ (done)
  │                   extract_booking_result   (inline 상태 갱신)
  │                           │
  └─[should_book=False]──► notification ──[tool_calls?]──► tools ──► notification
                              │
                              ▼ (done)
                             END
```

---

## 파일 구성

| 파일 | 역할 |
|------|------|
| `run.py` | 모니터링 루프 진입점, CLI 파싱, `MonitorCriteria` 정의 |
| `workflow.py` | LangGraph `StateGraph` 빌더, 라우팅 로직 |
| `agents.py` | 4개 에이전트 노드 함수, `extract_booking_result` |
| `state.py` | `FlightState` TypedDict 정의 |
| `mock_api.py` | Mock 항공권 검색/예약 HTTP 서버 (`ThreadingHTTPServer`) |

---

## 실행

### 기본 실행
```bash
python -m examples.flight_monitor.run
```

기본 설정: `ICN → NRT`, 임계값 `$280`, 최대 10회 체크, 3번째·7번째 체크에서 딜 발생.

### 커스텀 설정
```bash
python -m examples.flight_monitor.run \
  --origin ICN \
  --dest BKK \
  --date 2026-08-01 \
  --max-price 350 \
  --interval 5 \
  --max-checks 8 \
  --cheap-on 2 5
```

### CLI 옵션

| 옵션 | 기본값 | 설명 |
|------|--------|------|
| `--origin` | `ICN` | 출발지 IATA 코드 |
| `--dest` | `NRT` | 목적지 IATA 코드 |
| `--date` | `2026-07-15` | 여행 날짜 (YYYY-MM-DD) |
| `--max-price` | `280.0` | 최대 허용 가격 (USD) |
| `--passenger` | `Agentic AI Traveler` | 승객 이름 |
| `--interval` | `8` | 체크 간격 (초) |
| `--max-checks` | `10` | 최대 체크 횟수 |
| `--cheap-on` | `3 7` | 딜이 발생할 체크 번호 (Mock 전용) |

---

## 출력 예시

```
═════════════════════════════════════════════════════════════════
✈️  FLIGHT MONITOR — AGENTIC AI
═════════════════════════════════════════════════════════════════
  Route       : ICN → NRT
  Date        : 2026-07-15
  Max price   : $280.00 USD
  Interval    : every 8s
  Deal checks : [3, 7] (mock simulation)
═════════════════════════════════════════════════════════════════

─────────────────────────────────────────────────────────────────
  CHECK 1/10  [14:02:03]
─────────────────────────────────────────────────────────────────
  [INFO] Check #1: ICN→NRT | Cheapest: $347.21 USD | Threshold: $280.00 USD
  ⏭  No deal this check. Cheapest: $347.21 USD
  ⏳ Next check in 8s...

─────────────────────────────────────────────────────────────────
  CHECK 3/10  [14:02:20]
─────────────────────────────────────────────────────────────────
  [INFO] Check #3: ICN→NRT | Cheapest: $198.45 USD | Threshold: $280.00 USD
  ✅ BOOKED! Reference: AGNT48271  Price: $198.45 USD

═════════════════════════════════════════════════════════════════
🎉  MONITORING COMPLETE — BOOKING CONFIRMED!
    Booking reference : AGNT48271
    Final price       : $198.45 USD
    Savings           : ~$81.55 USD
    Found on check    : 3 of 10
═════════════════════════════════════════════════════════════════
```

---

## 환경 변수

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `OPENAI_API_KEY` | — | 필수 |
| `NOTIFICATION_DRY_RUN` | `true` | `true`이면 Slack/이메일 발송 없이 콘솔 출력 |
| `EMAIL_RECIPIENT` | `traveler@example.com` | 예약 확인 이메일 수신자 |

---

## 주요 설계 포인트

### 단일 ToolNode + `active_phase` 라우팅
하나의 공유 ToolNode가 모든 에이전트의 tool call을 처리합니다. 각 에이전트는 `active_phase` 필드를 설정하여 tool 실행 완료 후 어느 에이전트로 복귀할지 알려줍니다.

```python
# workflow.py — tools 노드에서의 복귀 라우팅
def _route_after_tools(state: FlightState) -> str:
    return state.get("active_phase", "search")
```

### Pydantic Structured Output (PriceAnalysisAgent)
`PriceAnalysisAgent`는 tool call 없이 LLM structured output(`_PriceDecision`)으로 의사결정합니다. 불필요한 LLM 왕복을 제거하여 비용을 절감합니다.

```python
class _PriceDecision(BaseModel):
    should_book: bool
    cheapest_price: float
    cheapest_airline: str
    cheapest_flight_id: str
    ...

llm = ChatOpenAI(...).with_structured_output(_PriceDecision)
```

### 암호화 Credential 관리
`BookingAgent`는 `auth_get_key`로 Fernet 암호화된 API 키를 복호화하여 사용합니다. 키는 실행 시작 시 `auth_store_key`로 볼트에 저장됩니다.

```python
# run.py
auth_store_key.invoke({"service": "flight-api", "key": "demo-api-key-xyz"})

# agents.py — BookingAgent의 tool set
BOOKING_TOOLS = [http_post, memory_get, memory_set, auth_get_key, notify_console]
```

### MockFlightAPI 내장 HTTP 서버
`ThreadingHTTPServer`로 구현된 로컬 mock API가 백그라운드 스레드로 실행됩니다. 에이전트는 실제 외부 API와 동일한 `http_get`/`http_post` 인터페이스를 사용합니다.

```python
api = MockFlightAPI(port=18990, cheap_on_checks=[3, 7]).start()
# GET  /api/flights/search?origin=ICN&destination=NRT&date=...&max_price=280
# POST /api/flights/book  { flight_id, airline, price, passenger_name, ... }
# GET  /api/health
```

`cheap_on_checks` 파라미터로 몇 번째 체크에서 딜이 발생할지 지정 → 실제 API 없이 전체 예약 플로우를 결정론적으로 테스트 가능합니다.

### 모니터링 루프 분리
그래프는 단일 체크 사이클만 담당하고, 루프 반복은 `run.py`에서 관리합니다. 그래프를 독립적으로 재사용하거나 단위 테스트하기 쉽습니다.

```python
# run.py
app = build_flight_graph()
for check in range(1, criteria.max_checks + 1):
    result = app.invoke(_build_initial_state(criteria, api, check))
    if result.get("booking_confirmed"):
        break
    time.sleep(criteria.check_interval_sec)
```

### `extract_booking_result` 인라인 상태 갱신
BookingAgent의 tool call 완료 후, LLM 호출 없이 메모리에서 예약 확인 정보를 읽어 `FlightState`를 갱신합니다. NotificationAgent가 `booking_reference`와 `confirmed_price`를 프롬프트에서 직접 참조할 수 있게 됩니다.
