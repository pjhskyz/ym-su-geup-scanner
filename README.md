# YM리서치 수급 스캐너

기관·외국인 일별 순매수와 시가총액 대비 수급 비율을 함께 확인하는 정적 사이트입니다.
현재 서비스: https://ymresearch-scanner.netlify.app/

## 갱신 시각과 상태

- GitHub Actions는 **평일 20:20 KST**(`20 11 * * 1-5`, UTC)에 수집을 시작하도록 예약합니다.
- **20:50 KST**에 보완 조회합니다. 스케줄러 지연과 수집·배포 소요 시간이 있어 20:20 화면 반영 완료를 보장하는 설정은 아닙니다.
- 기존 외부 cron의 **20:05 KST 요청은 실행을 유지하며 20:20까지 기다린 뒤 수집을 시작**합니다. 외부 cron의 예약 자체는 변경하지 않았습니다. 일반 `workflow_dispatch` 요청은 평일 20:00~20:19에 들어오면 최대 20분 대기하고, 20:20 이후에는 바로 진행합니다. 18:30 등 그보다 이른 요청과 주말 요청은 수집을 건너뜁니다.
- GitHub의 20:20·20:50 예약은 보완 경로입니다. GitHub 스케줄러가 늦게 실행해 **자정이나 주말로 넘어가도 유효한 예약 요청을 버리지 않습니다**. 두 경로 모두 실제 작업 시작이 늦어질 수 있으며, 20:20은 수집 시작 목표 시각입니다.
- Actions의 `collection_window` 작업 요약에서 허용·대기·건너뜀 사유를 확인할 수 있습니다. 건너뛴 요청은 데이터와 마지막 수집 상태를 덮어쓰지 않습니다.
- 기준 거래일(`asof`), 데이터 생성 시각(`metadata.updated_at`), 마지막 수집 시도(`scanner_status.json`)를 구분합니다.
- KRX 인증·조회 실패 시 빌드는 실패로 종료하고 **마지막 정상 `scanner_data.json`을 유지**합니다. 별도 상태 파일을 발행해 최신 수집 실패를 표시합니다.
- KRX 최근 거래일에 수급이 아직 없다면 직전 거래일로 폴백하며 `metadata.asof_fallback`에 기록합니다.

## 순위의 정확한 의미

| 항목 | 정의 |
|---|---|
| 원천 전체 금액 순위 | KOSPI·KOSDAQ 해당 투자자의 순매수금액이 0이 아닌 전종목을 금액 내림차순으로 정렬한 순번입니다. 시총 500억원 및 화면 필터 적용 전입니다. 동률은 종목코드 오름차순입니다. |
| 기존 0.4% 구간 | `ceil(순번 / 모수 × 100 / 0.4)`. 같은 구간 번호에 여러 종목이 들어가며 실제 등수가 아닙니다. 종목 자체의 과거 수급 강도도 아닙니다. |
| 화면 Top 30 | 스캐너 수록 종목 중 양수 순매수를 비율(I1/F1) 또는 금액으로 정렬한 상위 30개입니다. 종목 수록 조건은 해당 거래일 시가총액 500억원 이상입니다. 따라서 원천 전체 금액 순위와 모수가 다릅니다. |
| 신규·유지·이탈 | 동일한 선택 기준의 직전 거래일 Top 30과 비교합니다. 하루의 여러 실행 결과끼리 비교하지 않습니다. 과거 이력 없는 ‘최초 포착’이나 ‘재진입’으로 확대 해석하지 않습니다. |

기존 JSON `rows`의 28칸과 구간 필드는 다른 소비자와의 호환을 위해 유지합니다. 정확한 금액 순위는 `rank_details[종목코드].inst/frgn.today/previous/two_days_ago`에 별도로 제공합니다. 순위가 없는 종목은 `null`이며 마지막 등수로 간주하지 않습니다.

## 수급 비율과 강한 쌍끌이

- I1/I5/I20: 기관 1·5·20거래일 누적 순매수금액 ÷ **기준일 시가총액** × 100(%).
- F1/F5/F20: 외국인의 동일 계산입니다.
- 기본 강한 쌍끌이: **기관과 외국인의 당일 비율이 각각 0.3% 이상**입니다. `rank_details.ratio_pct`의 반올림 전 값으로 판단합니다. 예를 들어 실제 0.299%는 화면에 0.30%로 보여도 통과하지 않습니다.
- 금액·비율 원값은 `rank_details.net_won/ratio_pct/cap_won`에 보관합니다. 표시용 `rows`는 기존처럼 백만원/소수점 둘째 자리/시총 억원 단위를 유지합니다.
- 신규 진입 비교는 직전 거래일의 **실제 원 순매수와 그날 시가총액**으로 `metadata.comparison`을 구성합니다. 현재 시총으로 과거 비율을 역산하지 않습니다. 자료 누락 시 비교 대기를 표시합니다.
- 기존 발행 파일에 원값 sidecar가 없다면 **백만원 단위 순매수금액과 억원 단위 시가총액으로 비율을 근사 복원**합니다. 이미 반올림된 값을 사용하므로 0.3% 경계와 근접한 순위는 실제 원값 계산과 다를 수 있습니다. 복구 데이터는 `method: published_snapshot`, `precision: published_rounded_amount_and_cap`으로 구분하며, 다음 정상 수집에서 제공하는 반올림 전 원값을 우선 사용합니다.

## 업종 분류 보존

WICS 조회가 비거나 일부 종목을 누락하면 마지막 발행본에서 확인된 동일 종목코드의 업종만 유지합니다. 과거 분류의 확인일은 반복 수집 후에도 유지하며, 화면에 보완 분류일·종목 수·미분류 수를 표시합니다. 처음 보는 종목의 업종은 추정하지 않습니다. `metadata.sector_classification`에 당일 조회일과 종목별 원분류 확인일을 별도로 기록합니다.

## 원천 집계 범위

빌더는 pykrx의 `get_market_net_purchases_of_equities`를 이용합니다. pykrx 원천은 KRX `MDCSTAT02401`이며 조회일·시장·투자자 구분을 전달합니다. 기관은 기관합계(7050), 외국인은 외국인(9000, 기타외국인 제외)입니다.

현재 호출에는 거래 세션 또는 ATS 범위 선택이 없으며 원천 확정 시각을 확인할 응답 필드도 없습니다. 따라서 **20시까지의 시간외 거래 및 NXT 합산 여부는 미확인**으로 표시합니다. 20:20에 실행했다는 사실만으로 전체 거래 반영 완료라고 표시하지 않습니다.

참고 원천 코드:

- https://github.com/sharebook-kr/pykrx/blob/master/pykrx/website/krx/market/core.py (`투자자별_순매수상위종목`)
- https://github.com/sharebook-kr/pykrx/blob/master/pykrx/website/krx/market/wrap.py (`get_market_net_purchases_of_equities_by_ticker`)

## 주요 파일과 운영

| 파일 | 역할 |
|---|---|
| `index.html` | 수급 조회 화면 |
| `build_scanner_data.py` | 순매수·순위·직전 거래일 비교·집계 메타데이터 생성 |
| `scanner_data.json` | 마지막 정상 수급 데이터 |
| `scanner_status.json` | 최근 수집 시도 시각·성공/실패·마지막 정상 거래일 |
| `build_market.py` | 시장 지표와 개별 종목 이력 생성 |
| `.github/workflows/build.yml` | 수집·커밋 예약 |
| `collection_window.py` | 외부 요청의 20:20 대기 및 지연된 GitHub 예약 허용 |
| `test_collection_window.py` | 시간·요일 경계와 실제 대기·작업 출력 검증 |
| `test_scanner_data.py` | 순위·직전일 비교·실패 보존 검증 |

GitHub 저장소 Secrets의 `KRX_ID`, `KRX_PW`로 인증합니다. KRX가 비밀번호 변경을 요구하면 계정 비밀번호 변경과 Secret 갱신이 필요합니다. 인증정보는 JSON에 기록하지 않습니다. 수동 수집은 Actions의 `build-scanner-data` → `Run workflow`에서 실행합니다. 예정 시각 전이나 주말에 점검하려면 **‘예정 시각 전 수동 수집’(`force`)**을 켭니다. 기본값은 꺼짐이며, 명시적으로 켠 수동 실행만 시간·요일 제한을 우회합니다.

```bash
pip install pykrx finance-datareader pandas
python -m unittest test_scanner_data.py test_collection_window.py
python build_scanner_data.py
python -m http.server 8000
```

미리보기는 http://localhost:8000/ 에서 확인합니다. JSON 조회 실패 시 과거 시드 데이터를 최신 데이터처럼 표시하지 않습니다.
