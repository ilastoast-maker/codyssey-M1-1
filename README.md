# 범용 주식 시계열 트렌드 분석

기본 제출물은 대만증권거래소 TSMC 본주(2330.TW)의 2021~2025년 분석입니다. 추가로 티커를 입력하면 미국 주식 등에도 같은 가격 추세, 수익률, 변동성, 거래량, 이상치, 분해와 기준선 예측을 적용할 수 있습니다.

## 결과물

- `REPORT.md`: 수치 근거가 포함된 최종 분석 리포트
- `src/pipeline.py`: 공식 데이터 수집, 정제, 분석, 시각화, 리포트 생성을 한 번에 수행
- `app.py`: 기간과 분석 창을 변경할 수 있는 Streamlit 대시보드
- `images/`: 핵심 시각화 4개와 보너스 시각화 2개
- `data/`: 원본·처리 데이터와 재현용 핵심 지표
- `tests/`: 정제, 이동평균, 예측 데이터 누수 방지 테스트
- `test-results/`: 마지막 검증 결과, JUnit XML, 전체 파이프라인 스모크 출력
- `outputs/<ticker>/`: 사용자가 입력한 다른 종목의 데이터·그래프·리포트

## 분석 질문

1. 장기 추세와 주요 추세 전환 구간은 언제인가?
2. MA20 ≥ MA60 국면과 반대 국면의 수익률·변동성은 다른가?
3. 급등·급락 이상치는 언제인가?
4. 거래량 상위 10% 거래일의 절대수익률은 더 큰가?
5. 월별 반복 패턴이 관찰되는가?

## 설치 및 실행

Python 3.10 이상을 사용합니다.

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
python -m pip install -r requirements.txt
python -m src.pipeline
```

다른 종목 분석:

```bash
# NVIDIA: Yahoo Finance 수정주가 우선 사용
python -m src.pipeline --ticker NVDA --name NVIDIA --currency USD

# Apple, 분석 기간 변경
python -m src.pipeline --ticker AAPL --name Apple \
  --start 2020-01-01 --end 2025-12-31 --currency USD

# 대만 상장 종목은 TWSE 공식 API 자동 선택
python -m src.pipeline --ticker 2330.TW --name TSMC --provider auto --currency TWD
```

지원 옵션:

| 옵션 | 설명 |
|---|---|
| `--ticker` | 분석할 티커 |
| `--name` | 리포트와 그래프에 표시할 기업명 |
| `--provider` | `auto`, `twse`, `yahoo` |
| `--start`, `--end` | 분석 기간 |
| `--currency` | 그래프 가격 단위 |
| `--skip-download` | 해당 티커의 캐시된 원본 CSV 재사용 |

다른 종목의 결과는 기존 TSMC 제출물을 덮어쓰지 않고 `outputs/<ticker>/`에 저장됩니다.

이미 저장된 원본 CSV로 네트워크 호출 없이 다시 분석하려면:

```bash
python -m src.pipeline --skip-download
```

테스트:

```bash
pytest -q
```

마지막으로 커밋된 테스트 결과는 [`test-results/TEST_RESULTS.md`](test-results/TEST_RESULTS.md)에서 확인할 수 있습니다. 문제가 생기면 당시 실행 환경과 현재 환경의 패키지 버전 및 스모크 출력부터 비교하세요.

대시보드:

```bash
streamlit run app.py
```

## 보너스 과제

### 탐색형 대시보드

사이드바에서 티커, 날짜 범위, 단기·장기 이동평균, 변동성 창을 변경할 수 있습니다. `2330.TW`는 저장된 공식 TWSE 데이터를 사용하고, 다른 티커는 저장된 분석 결과를 우선 사용한 뒤 없으면 Yahoo Finance에서 내려받습니다.

1. 전체 기간에서 장기 추세 확인
2. 변동성이 높은 구간으로 기간 축소
3. 이동평균 창 변경 후 국면 차이 비교
4. 거래량 상위 10일과 수익률 확인
5. `NVDA`, `AAPL` 등으로 티커를 바꿔 동일 분석 비교

### 시계열 심화

- 월말 종가 가법 분해: 추세·12개월 계절성·잔차
- 마지막 60거래일 홀드아웃: 마지막 값 유지·선형 드리프트 기준선 비교

예측은 투자 모델이 아니라 복잡한 모델이 넘어야 할 최소 기준선입니다.

## 데이터와 라이선스 주의

- 출처: [Taiwan Stock Exchange Corporation](https://www.twse.com.tw/)
- API: `exchangeReport/STOCK_DAY`
- 기간: 2021-01-01 ~ 2025-12-31
- 빈도: 실제 거래일 기준 일별 데이터
- 가격: TWSE 종가(TWD). 현금배당 재투자를 반영한 총수익률이 아님
- 휴장일: 보간하지 않음

미국 주식은 Yahoo Finance의 `Adjusted Close`를 우선 사용해 주식분할과 배당 조정 영향을 줄입니다. Yahoo Finance 호출은 간헐적으로 제한될 수 있으므로 CLI로 생성한 원본 CSV를 보존하고 `--skip-download`를 사용할 수 있습니다.

TWSE 데이터 이용약관과 출처 표시 조건은 제출·재배포 전에 다시 확인해야 합니다. 이 프로젝트는 교육용이며 투자 조언이 아닙니다.
