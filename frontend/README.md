# React frontend

오늘의 시간별 ASOS 관측과 내일 평균기온 AI 예측을 한 화면에 표시하는 React + TypeScript 앱입니다. 프로덕션 빌드는 Nginx가 제공하며 `/api` 요청은 FastAPI로 프록시됩니다.

개별 빌드 확인:

```powershell
npm install
npm run build
```

전체 서비스 실행은 프로젝트 루트의 `scripts/start.ps1`을 사용합니다.
