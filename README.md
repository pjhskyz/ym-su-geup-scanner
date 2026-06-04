# 기관 수급 스캐너

기관 누적 순매수 순위의 **시계열 점프**를 잡아내는 수급 스캐너.
pykrx 공개데이터로 매일 저녁 자동 갱신되고, GitHub Pages로 정적 호스팅된다.

## repo 구조

```
ym-su-geup-scanner/
├─ index.html                    # 화면 (su-geup-scanner.html 을 이 이름으로)
├─ scanner_data.json             # Actions가 매일 생성/갱신 (최초엔 없어도 됨)
├─ build_scanner_data.py         # 데이터 빌더
└─ .github/
   └─ workflows/
      └─ build.yml               # 매일 저녁 자동 실행 워크플로
```

> `build.yml` 은 반드시 `.github/workflows/` 아래에 둬야 인식된다.
> 화면 파일은 `index.html` 로 두면 Pages 루트(`https://<id>.github.io/<repo>/`)로 바로 열린다.

## 셋업 (1회)

1. **repo 생성** 후 위 4개 파일을 구조대로 커밋.
2. **KRX 계정** 발급 — 2025-12-27 회원제 전환으로 로그인 필수(조회는 무료).
3. **Secrets 등록** — repo `Settings → Secrets and variables → Actions → New repository secret`
   - `KRX_ID` = KRX 아이디
   - `KRX_PW` = KRX 비밀번호
4. **Pages 켜기** — `Settings → Pages → Build and deployment`
   - Source: **Deploy from a branch**
   - Branch: **main / (root)**
5. **첫 실행** — `Actions` 탭 → `build-scanner-data` → `Run workflow` (수동).
   성공하면 `scanner_data.json` 이 커밋되고 Pages가 자동 재배포된다.

이후로는 **매주 월~금 20:00 KST** 에 자동으로 데이터가 갱신된다.

## 동작 원리

- `build_scanner_data.py` 가 기관·외국인 각각 D / D-1 / D-2 기준일마다 1년 윈도우
  누적순매수를 전종목 집계 → 순위 → 백분위 버킷으로 바꾸고, 상위 종목의
  수익률·연속 순매수일(기관/외국인)·업종을 보강해 `{asof, inst, frgn}` 으로 저장한다.
- `index.html` 은 `scanner_data.json` 을 fetch 해서 기관/외국인 탭으로 표를 그린다.
  파일이 없거나 로컬(file://)로 열면 내장 시드(2026-06-02, 기관)로 표시된다.

## 로컬 미리보기

`file://` 로 열면 fetch가 막혀 시드만 보인다. 라이브 JSON까지 확인하려면:

```bash
python -m http.server 8000   # repo 루트에서
# http://localhost:8000/ 접속
```

## 튜닝 포인트

- `build_scanner_data.py` 상단: `LOOKBACK_DAYS`(1/2/3년), `TOP_N`, `BUCKET_SIZE`, `INVESTOR`("기관합계"↔"외국인")
- 백분위 모수는 todaytoppick 내부 로직과 정확히 같지 않은 근사값.
