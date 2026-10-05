# 상명 — 로컬 기상 관측·AI 기온 예측 서비스

기상청 서울 ASOS 관측자료를 조회하고, 학습된 일자료 기반 모델로 내일의
평균기온을 예측합니다. React 화면, FastAPI, MySQL을 Docker Compose로 실행합니다.

## 현재 화면과 기능

- 상단: 태극기 로고, 한국 표준시(KST) 날짜·시계, **예측 재시도** 버튼
- 날씨 패널: 어제 확정 일자료 / 오늘 시간별 관측 / 내일 평균기온 예측
- 하단 분석 창: 실제 모델별 출력, 가중합 계산식, 입력 기간, 44개 기본
  변수의 원값·결측 대체값, 주요 파생변수, 배포 모델 평가와 최근 재학습 비교
- 예측 결과와 계산 입력 스냅샷의 MySQL 저장, 동일 날짜·모델 결과의 upsert
- 관측 요청 일부가 실패해도 다른 패널과 모델 근거는 독립적으로 표시

좁은 화면에서는 날씨 패널이 세로로 배치됩니다. **예측 재시도는 재추론이며
재학습이 아닙니다.** 관측 응답에는 기본 10분 캐시가 적용됩니다.

## 데이터와 모델

| 용도 | 현재 자료 경로 |
|---|---|
| 어제 관측 | API Hub ASOS 일자료 (`kma_sfcdd.php`) |
| 오늘 관측 | API Hub ASOS 시간자료 (`kma_sfctm2.php`) |
| 예측 입력 | 기존 ASOS 최근 일자료 + API Hub 최신 일자료 병합 |
| 로컬 재학습 | 기존 공공데이터포털 ASOS 장기 일자료, 2000년 이후 |

등록된 API Hub 주소는 ZIP 다운로드가 아닌 **EUC-KR 텍스트 API**입니다.
백엔드가 URL의 예제 날짜·관측소를 실제 조회 조건으로 교체합니다.
기본 관측소는 서울 `108`이며, API Hub 일자료에 없는 항목은 기존 전처리로
대체합니다. 실제 대체 내역은 하단 분석 창에서 확인할 수 있습니다.

현재 모델은 어제 관측일 `t`에서 `t+2`, 즉 사용자 관점의 내일 평균기온을
예측합니다. 오늘 시간자료는 화면 표시용이며 **모델 학습 입력에는 포함되지
않습니다.** 관측 자료가 미공개이거나 예측 대상 날짜가 맞지 않으면 임의의
값을 생성하거나 내일 예측으로 저장하지 않습니다.

- 배포 버전: `ensemble-asos-daily-v2-20260924-044259`
- 모델: CatBoost·LightGBM 기온 변화량 예측의 가중 앙상블 (0.26 / 0.74)
- 변수: 기본 44개 → 파생 포함 247개 → 결측 표시 포함 최종 442개
- 전처리: 날짜 정렬·중복 제거, 숫자 변환, 강수·적설 등 결측 0 대체,
  나머지 과거값 ffill, 잔여 결측 학습 중앙값 대체
- 현재 구현에는 별도 통계적 이상치 제거 단계가 없습니다.

배포 아티팩트에 기록된 MAE는 약 **2.163°C**입니다. 최근 로컬 재학습은
동일 평가 구간(입력일 기준 2025-01-01 ~ 2026-10-02, 640행)에서 기존
**2.174°C**, 후보 **2.183°C**로 후보가 나빠 기존 모델을 유지했습니다.
서로 다른 평가 기간의 점수를 직접 비교하거나 MAE를 개별 예측의 보장
오차로 해석하지 않습니다. 분석 창은 계산 경로이며 SHAP·인과 설명은 아닙니다.

## 실행과 환경변수

필요 조건: Linux 컨테이너 모드의 Docker Desktop.

프로젝트 루트에서 `.env`가 없을 때만 예제를 복사합니다. 이미 입력한
설정 파일을 덮어쓰지 마세요.

```powershell
if (-not (Test-Path -LiteralPath .env)) { Copy-Item .env.example .env }
```

`.env`에 다음 값을 입력합니다. 인증키를 URL에 포함하지 말고 별도 항목으로
입력하며, 기상청에서 해당 API 활용 승인이 필요합니다.

| 변수 | 역할 |
|---|---|
| `KMA_API_KEY` | 공공데이터포털 일반 인증키(Decoding); 장기 학습자료·예측용 최근 일자료 조회 |
| `KMA_APIHUB_AUTH_KEY` | API Hub 인증키; 위 키와 별개 |
| `KMA_APIHUB_HOURLY_FILE_URL` | API Hub 시간 관측 URL; `authKey` 제외 |
| `KMA_APIHUB_DAILY_FILE_URL` | API Hub 일 관측 URL; `authKey` 제외 |
| `KMA_STATION_ID`, `KMA_STATION_NAME` | 기본 `108`, `서울` |
| `MYSQL_*` | 로컬 DB 이름·계정·비밀번호·호스트 포트 |

API Hub URL이 비어 있으면 기존 ASOS 경로를 사용하지만 해당 시간자료 API는
당일 조회가 불가능합니다. 오늘 날씨 표시는 API Hub 설정을 사용하세요.

```powershell
docker compose up -d --build --wait --wait-timeout 120
```

- 웹: [localhost:3000](http://localhost:3000)
- API 문서: [localhost:8000/docs](http://localhost:8000/docs)
- 상태 확인: [localhost:8000/health](http://localhost:8000/health)
- MySQL: `127.0.0.1:3307` (컨테이너 내부 `3306`)

환경변수만 수정한 경우 `docker compose up -d --force-recreate backend`로
적용합니다. 코드·모델·평가 기록 변경은 해당 이미지를 재빌드해야 합니다.
중지는 `docker compose down`이며 MySQL 데이터는 명명된 볼륨에 유지됩니다.
`scripts/start.ps1`, `scripts/stop.ps1`도 같은 폴더에서 사용할 수 있습니다.

## 현재 API

| 메서드 | 경로 | 역할 |
|---|---|---|
| GET | `/health` | DB·배포 모델 상태 |
| GET | `/api/v1/dashboard` | 세 날씨 패널, 예측 계산 기록, 모델 평가, 최근 예측 |
| GET | `/api/v1/weather/yesterday` | 어제 일자료 |
| GET | `/api/v1/weather/today` | 오늘 요약·시간별 관측 |
| GET | `/api/v1/weather/hourly` | 오늘 시간별 관측 |
| POST | `/api/v1/predictions/run` | 기존 모델 재추론·DB 저장·계산 기록 반환 |
| GET | `/api/v1/predictions/history?limit=7` | 저장된 예측 이력 |

프론트는 대시보드 응답 하나로 날씨와 분석 창을 갱신합니다. 계산 기록은
`tomorrow.calculation`, 공개 가능한 모델 요약은 `model_evidence`에 있습니다.

## 로컬 재학습과 검증

수집 캐시·시간순 분할·후보 모델의 교체 조건·평가 기록은
[로컬 재학습 안내](docs/local-training.md)를 참고하세요. 재학습은 별도 CLI
작업이며 후보가 기존 모델과 지속성 기준선보다 좋아야 교체 조건을 통과합니다.
현재 스크립트는 후보를 저장하고 평가할 뿐 자동 배포하지 않습니다.

```powershell
docker compose run --rm --no-deps -T -e PYTHONPATH=/app -v "${PWD}/backend/app:/app/app:ro" -v "${PWD}:/workspace" backend python -m app.train_v2
```

테스트는 로컬 MySQL 컨테이너가 실행 중인 상태에서 진행합니다.

```powershell
docker compose run --rm --no-deps -T -e PYTHONPATH=/app -v "${PWD}/backend/tests:/app/tests:ro" backend python -m unittest discover -s /app/tests -p "test_*.py"
```

## 파일 구성

```text
frontend/                    React·TypeScript·Vite, Nginx 배포
backend/app/                 API, 자료 수집·전처리·추론·계산 근거
backend/app/train_v2.py       로컬 수집·재학습·후보 평가
backend/tests/               API Hub 보안·계산식·DB 회귀 테스트
artifacts/v2/                현재 배포 모델과 안전한 최근 평가 요약
data/local/                  로컬 수집 캐시 (Git 제외)
artifacts/candidates/        재학습 후보 (Git 제외)
database/schema.sql          모델 버전·예측 스키마, 예약된 실행 이력 테이블
docs/local-training.md       현재 로컬 학습·평가·분석 창 안내
scripts/                     로컬 기동·중지 도우미
docker-compose.yml           frontend / backend / mysql
```

실제 관측값의 자동 사후 입력과 `pipeline_runs` 자동 기록은 아직 구현되지
않았습니다. 현재 DB에는 예측 결과·모델 버전·추론 입력 스냅샷을 저장합니다.

## 보안

- `.env`와 변형 환경파일, 인증 파일, 로컬 원자료, 후보·백업 모델은 Git에서 제외합니다.
- `.env.example`은 빈 인증키만 제공하며, 현재 검증된 배포 모델과 안전한 평가 요약만 추적합니다.
- 인증값은 백엔드에만 주입합니다. 브라우저·오류 메시지·학습 메타데이터에 인증 URL을 표시하지 않습니다.
- API Hub 요청은 HTTPS·허용 도메인·지원 경로로 제한합니다.
- Docker 빌드 컨텍스트에서도 환경파일·원자료·로컬 백업을 제외합니다.
- 서비스 포트는 로컬 루프백에만 바인딩됩니다. 외부 공개용 인증·접근 제어는 별도 구현이 필요합니다.
- 기본 DB 비밀번호는 개발용입니다. 실제 운영에는 반드시 변경하세요.

커밋 전 현재 설정된 API 인증값의 유출 여부와 환경파일 추적 여부를 검사할 수 있습니다.

```powershell
.\scripts\check-secrets.ps1 -IncludeHistory
```

검사 대상은 현재 로컬 인증값과 추적 텍스트·스테이징 변경·로컬 Git 이력의
텍스트 패치입니다. 알려지지 않은 과거 키나 바이너리 내부 비밀값의 부재까지
보증하는 검사는 아닙니다. 인증값 자체는 검사 출력에 표시하지 않습니다.

키를 실수로 커밋했다면 `.gitignore` 추가만으로 과거 이력에서 제거되지
않습니다. 해당 키를 폐기·재발급하고 별도로 이력을 정리해야 합니다.
