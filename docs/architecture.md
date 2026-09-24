# 서비스 아키텍처

## 데이터 흐름

```text
기상청 ASOS 시간자료 ──┐
                       ├─> FastAPI /api/v1/dashboard ─> React 오늘의 날씨
기상청 ASOS 일자료 ───┘             │
                                    ├─> V2 feature engineering
                                    ├─> CatBoost + LightGBM residual ensemble
                                    └─> MySQL temperature_predictions
                                                  │
                                                  └─> React 내일 기온/최근 이력
```

프론트는 화면 진입 시 대시보드 엔드포인트 하나만 요청합니다. 백엔드는 시간자료와 일자료 요청을 동시에 실행하고, 기상청 응답은 기본 10분간 메모리에 캐시합니다.

## 컨테이너

| 서비스 | 이미지/빌드 | 포트 | 책임 |
|---|---|---:|---|
| `frontend` | Node 22 build → Nginx | 3000 | React 정적 배포, `/api`를 backend로 proxy |
| `backend` | Python 3.12 slim | 8000 | KMA 호출, 전처리, 추론, DB 저장 |
| `mysql` | MySQL 8.4 | 3306 | 예측·모델 버전·실행 이력 보존 |

`depends_on`과 health check로 MySQL → FastAPI → 프론트 순서로 준비됩니다.

## 예측 계약

- 관측소: 기본 서울 `108`
- 모델 입력 원천: 확정된 ASOS 일자료 45일
- 기준일: 최신 확정 일자료인 어제(`t`)
- 타깃: `t+2` 평균기온, 사용자 관점의 내일
- 원본 변수: 44개
- 특성 집합: 원본 44개와 lag 1·2·3·7·14일, rolling 3·7·14일, 변화량, 계절성, 물리 파생값을 포함해 247개
- 전처리 후 입력: 결측 표시 변수를 포함한 442개
- 모델: CatBoost 0.26 + LightGBM 0.74 residual 앙상블
- 오프라인 테스트 MAE: 약 2.16°C

오늘의 시간자료는 표시 목적으로만 사용합니다. 일자료 기반 모델 입력과 분리되어 있으므로 오늘 관측을 내일 예측에 섞는 정보 누수가 없습니다.

## DB 스키마

- `model_versions`: 모델 버전, 유형, 관측소, 성능, 메타데이터
- `temperature_predictions`: 입력 기준일, 예측 대상일, 예측값, 추후 실제값, 입력 스냅샷
- `pipeline_runs`: 향후 스케줄러와 배치 실행 감사를 위한 실행 상태

`(station_id, predicted_for_date, model_version)` unique key로 새로고침에 의한 중복 저장을 막고 같은 예측은 upsert합니다.

## 다음 단계: 시간자료 기반 모델 강화

다음 작업에서는 현재 수집 경로를 재사용하되 별도의 학습 데이터셋을 구성합니다.

1. 일별 cutoff 시각을 고정해 그 시각까지의 시간자료만 사용합니다.
2. 최근 6·12·24시간 기온 변화, 습도·기압 추세, 강수, 풍향·풍속 통계를 생성합니다.
3. 현재 일자료 모델과 동일한 시간 분할 테스트 구간에서 성능을 비교합니다.
4. 일자료 모델보다 MAE가 개선된 경우에만 앙상블에 편입합니다.
5. 모델 버전과 입력 스키마를 새 버전으로 저장해 기존 운영 결과와 분리합니다.

## 보안과 운영

- KMA 키는 `.env`에서 FastAPI 컨테이너에만 주입합니다.
- 브라우저는 기상청 API를 직접 호출하지 않습니다.
- `.env`, 원자료, 모델 산출물은 Git 추적 대상에서 제외합니다.
- 외부 공개 전 DB 비밀번호 변경, HTTPS reverse proxy, API rate limit, 예측 스케줄러를 추가합니다.
