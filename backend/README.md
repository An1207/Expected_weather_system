# FastAPI backend

기상청 ASOS 일자료·시간자료 호출, V2 CatBoost–LightGBM 추론, MySQL 예측 저장을 담당합니다.

- 앱 진입점: `app/main.py`
- 기상청 클라이언트: `app/services/kma.py`
- 특성 생성: `app/services/features.py`
- 모델 로더·추론: `app/services/predictor.py`
- API 문서: 서비스 실행 후 http://localhost:8000/docs

로컬 전체 실행은 프로젝트 루트의 `scripts/start.ps1`을 사용합니다.
