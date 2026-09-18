# 테스트 실행 기록

## 실행 정보

- 실행 시각: 2026-09-18 12:45:06 UTC
- 테스트 대상 커밋: `44be7feea8c1c78a8d47aa58e8eb8f54fe478c53`
- 운영체제: Linux
- Python: 3.12.14
- 목적: 범용 티커 기능 추가 이후 회귀 테스트 및 TSMC 전체 파이프라인 검증

## 1. 단위 테스트

실행 명령:

```bash
python -m pytest -q --junitxml=test-results/pytest.xml
```

결과:

```text
....                                                                     [100%]
4 passed in 3.21s
```

검증 항목:

1. 데이터 정제와 20일·60일 이동평균 시작 위치
2. 예측 홀드아웃 데이터 누수 방지
3. TWSE/Yahoo 제공자 자동 선택과 안전한 티커 경로
4. 미국 주식 분석에서 수정주가(`Adjusted Close`) 우선 사용

JUnit 원본: [`pytest.xml`](pytest.xml)

## 2. Python 문법 검사

실행 명령:

```bash
python -m compileall -q src app.py tests
```

결과: 오류 없음.

## 3. 범용 파이프라인 스모크 테스트

저장된 TWSE 원본을 사용해 티커 입력부터 정제, 분석, 시각화, 예측, 리포트 생성까지 전체 흐름을 실행했다.

```bash
python -m src.pipeline \
  --ticker 2330.TW \
  --name TSMC \
  --currency TWD \
  --skip-download
```

핵심 결과:

| 항목 | 결과 |
|---|---:|
| 제공자 | TWSE |
| 거래일 | 1,214 |
| 실제 기간 | 2021-01-04 ~ 2025-12-31 |
| 중복 날짜 | 0 |
| 잘못된 OHLC 행 | 0 |
| 분석 가격 누락 제거 | 0 |
| 기간 가격 변화 | 189.1791% |
| IQR 이상치 | 48일 |

전체 출력: [`tsmc-smoke-output.json`](tsmc-smoke-output.json)

## 4. 실행 환경 패키지

```text
pandas==2.2.3
numpy==2.3.5
matplotlib==3.10.8
seaborn==0.13.2
scikit-learn==1.8.0
statsmodels==0.14.4
streamlit==1.41.1
yfinance==0.2.66
pytest==8.3.3
```

이 실행 환경에는 사전 설치된 패키지가 있어 일부 버전이 `requirements.txt`의 고정 버전보다 최신이다. 제출용 재현성 확인 시에는 새 가상환경에서 `requirements.txt`를 설치해 다시 실행해야 한다.

## 5. 알려진 외부 의존성 문제

Yahoo Finance를 이용한 미국 주식 실시간 다운로드는 테스트 환경에서 HTTP 429 호출 제한이 발생했다. 따라서 아래 항목을 구분한다.

- 통과: 제공자 선택, 수정주가 적용, 범용 분석 로직의 단위 테스트
- 통과: 저장된 CSV를 이용한 범용 전체 파이프라인
- 미확인: 이 실행 시점의 Yahoo Finance 실시간 다운로드 성공 여부

코드는 다운로드 재시도와 티커별 원본 CSV 캐시, `--skip-download`를 제공한다. Yahoo 호출 실패는 분석 로직 실패와 구분해서 확인해야 한다.

## 6. 파일 무결성

```text
8e260d5d507f3357b724067d3a23ccd226d512451c7b76505d64de99f23a9b51  pytest.xml
24e36fca7d1a83acec0720c0c1857a1b3ed092af8be901da853e8ab7c9f11a07  tsmc-smoke-output.json
```

