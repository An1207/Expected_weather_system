# 하늘결 — AI 기온 예측 로컬 서비스

기상청 ASOS 시간자료로 오늘 날씨를 보여주고, ASOS 일자료와 강화된 CatBoost–LightGBM 모델로 내일 평균기온을 예측하는 React + FastAPI + MySQL 서비스입니다.

## 현재 구현 범위

- 왼쪽 UI: 서울(ASOS 108) 오늘의 최신 관측, 최저·최고기온, 습도, 강수, 바람, 기압, 시간별 관측과 원본 변수
- 오른쪽 UI: 내일 평균기온 AI 예측, 모델 테스트 MAE, 입력 기준일, 최근 예측 이력
- FastAPI: 기상청 일자료·시간자료 API 호출, 캐시, V2 앙상블 추론, MySQL 저장, Swagger 문서
- AI: 기존 44개 변수에 lag·rolling·계절성·물리 특성을 더한 247개 특성, 결측 표시 포함 최종 입력 442개
- MySQL: 모델 버전, 날짜별 예측, 실제값, 파이프라인 실행 이력 스키마
- Docker Compose: React/Nginx, FastAPI, MySQL 8.4 컨테이너와 health check

시간자료는 현재 **오늘 날씨 표시용**입니다. 시간자료를 AI 입력으로 학습하는 작업은 다음 단계로 분리했습니다.

## 실행

필요 조건은 Docker Desktop입니다.

1. 프로젝트 루트에서 `.env.example`을 `.env`로 복사합니다.
2. `.env`의 `KMA_API_KEY=` 뒤에 공공데이터포털의 기상청 API 일반 인증키(Decoding)를 입력합니다.
3. PowerShell에서 실행합니다.

```powershell
cd "C:\Users\seho1\Desktop\대학자료\기상 예측 프로젝트"
.\scripts\start.ps1
```

접속 주소:

- 웹 화면: http://localhost:3000
- FastAPI 문서: http://localhost:8000/docs
- 상태 확인: http://localhost:8000/health
- MySQL: `localhost:3306`

중지:

```powershell
.\scripts\stop.ps1
```

MySQL 데이터는 `mysql_data` Docker 볼륨에 유지됩니다. DB까지 초기화할 때만 `docker compose down -v`를 직접 실행하세요.

## API

| 메서드 | 경로 | 역할 |
|---|---|---|
| `GET` | `/health` | DB·AI 모델 상태 |
| `GET` | `/api/v1/dashboard` | 화면에 필요한 오늘 관측·내일 예측·이력을 한 번에 반환 |
| `GET` | `/api/v1/weather/today` | 오늘 요약과 시간별 관측 |
| `GET` | `/api/v1/weather/hourly` | 오늘의 시간별 ASOS 관측 |
| `POST` | `/api/v1/predictions/run` | 최신 일자료로 예측 실행 및 DB upsert |
| `GET` | `/api/v1/predictions/history?limit=7` | 저장된 예측 이력 |

## 예측 시점

일자료는 하루가 끝난 뒤 확정되므로 운영 시점에는 어제 자료가 최신입니다. 모델은 관측일 `t`에서 `t+2` 평균기온을 예측합니다. 즉 오늘 서비스를 열면 **어제까지의 일자료로 내일 평균기온**을 예측합니다. 오늘 시간자료는 화면에만 사용되어 미래 정보 누수를 만들지 않습니다.

## 모델

- 버전: `ensemble-asos-daily-v2-20260924-044259`
- CatBoost residual + LightGBM residual 가중 앙상블
- 테스트 MAE: 약 `2.16°C`
- 모델 파일: `artifacts/v2/`
- 재학습 노트북: `ml/notebooks/02_daily_temperature_enhanced_v2_colab.ipynb`

## 주요 구조

```text
├─ frontend/                  # React + TypeScript + Vite, Nginx 배포
├─ backend/app/               # FastAPI, KMA 클라이언트, 전처리, AI 추론
├─ artifacts/v2/              # 실제 V2 모델·전처리기·메타데이터
├─ database/schema.sql        # MySQL 초기 스키마
├─ ml/notebooks/              # Colab 학습 노트북
├─ scripts/start.ps1          # 키 확인 후 전체 서비스 기동
├─ scripts/stop.ps1
└─ docker-compose.yml
```

세부 데이터 흐름은 `docs/architecture.md`에 정리했습니다.

## 환경변수

`.env`는 Git에서 제외됩니다. API 키를 React 코드나 저장소에 넣지 마세요.

- `KMA_API_KEY`: 공공데이터포털 일반 인증키(Decoding)
- `KMA_STATION_ID`: 기본 `108`(서울)
- `KMA_STATION_NAME`: 기본 `서울`
- `MYSQL_*`: 로컬 MySQL 계정과 DB 설정

운영 또는 외부 공개 시에는 `.env`의 기본 DB 비밀번호를 반드시 변경하세요.

## 문제 해결

- `/health`의 model이 `ready`가 아니면 `artifacts/v2/`의 5개 핵심 모델 파일이 있는지 확인합니다.
- 기상청 API 오류가 나면 API 키가 Decoding 키인지, ASOS 일자료·시간자료 활용 신청이 승인됐는지 확인합니다.
- Docker Desktop이 Linux engine 오류로 열리지 않으면 Docker Desktop을 완전히 종료하고 Windows를 재부팅한 뒤 다시 실행합니다.
