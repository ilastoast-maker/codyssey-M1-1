# 최종 검증 기록

- 실행 시각: 2026-10-17T09:46:37.868859+00:00
- Python: 3.12.14 / Linux
- 기준 커밋: `6795834a1dec73eba83522a56d10b6a2edb856fe`
- 검증 대상: 해당 커밋 위에 적용한 과제 완성 변경사항. [소스 체크섬](source-checksums.json)으로 실제 검증 파일을 식별한다.
- 환경: 새 가상환경에 `requirements.txt` 고정 버전 설치. 하위 의존성은 `requirements-lock.txt`에 기록.

## 실행 및 결과

| 검증 | 결과 |
|---|---|
| `python -m pytest -q --disable-warnings --junitxml=test-results/pytest.xml` | 19 passed |
| `python -m compileall -q src app.py tests` | 통과 |
| `python -m pip check` | No broken requirements found |
| `python -m src.pipeline --skip-download` | 1,214거래일, 2021-01-04~2025-12-31 |
| `python -m src.pipeline --ticker 2330.TW --name TSMC --start 2024-01-01 --end 2024-12-31 --skip-download` | 242거래일, 2024-01-02~2024-12-31 |
| 원본에서 독립 재계산 | 연율화 변동성·마지막값/드리프트 MAE 일치 |
| 변경 전 지표와 비교 | 기존 모든 분석 지표 동일(감사·추가 항목 제외) |
| 리포트·README·대시보드 문서의 로컬 링크 | 누락 없음 |
| 실제 Chromium 브라우저 조작 | 전체 기간·2024년·창 변경·거래량 표 캡처 완료 |

로그: [pytest 출력](pytest-output.txt), [JUnit](pytest.xml), [전체 기간 출력](tsmc-smoke-output.json), [2024년 출력](tsmc-2024-smoke-output.json).

Matplotlib의 Pyparsing 관련 사용 중단 예고 경고 14개가 발생했으며 실행 오류는 없었다. `--disable-warnings`는 요약을 생략할 뿐 테스트 실패를 숨기지 않는다.

## 회귀 검증 범위

- 이동평균 시작 위치, 일간 수익률 첫 관측값 처리.
- 홀드아웃 가격을 바꿔도 두 기준선 예측값이 변하지 않는지 확인.
- 제공자 자동 선택, 티커 경로 정규화.
- 수정주가 선택, Yahoo 날짜의 시간대 제거.
- 캐시 재사용 시 요청 기간 필터 및 수익률 시작점 재계산.
- 관측치 부족, 결측·중복·잘못된 OHLC 처리와 감사 수치.
- 기본 TWSE 캐시의 명시적 티커 실행, 신규 데이터 디스크 저장·재사용·명시적 갱신.
- 미수집 기간을 캐시가 포함한다고 잘못 판단하지 않는지 확인.
- Yahoo 429 예외를 빈 데이터 오류로 숨기지 않고 반복 호출 없이 안내.
- 대시보드 연도 필터·분석 창 변경·날짜 한 개 선택·짧은 기간·잘못된 이동평균 조합.

## 실제 서비스와 모의 검증의 구분

이번 최종 회귀 테스트는 재현성을 위해 포함된 TWSE 원본과 모의 Yahoo 응답을 사용했다. Yahoo 신규 다운로드의 성공을 주장하지 않는다. 앞선 검토에서 Yahoo 실호출은 429 제한으로 실패했고, TWSE 전체 재수집 1,214행은 포함된 원본과 모든 수치가 동일했다. 캐시 우선 동작은 외부 서비스 장애가 있어도 기본 과제를 실행하기 위한 것이다. 429 처리 테스트는 제어된 예외를 주입한 회귀 검증이다.

## 대시보드 보너스

[실제 실행 스크린샷 및 시나리오](../docs/DASHBOARD_DEMO.md)를 제출 방식 (3)의 증빙으로 제공한다. 외부 배포는 수행하지 않았다.
