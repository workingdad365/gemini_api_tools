@echo off
REM Google Gemini API Tools - Web Application 시작 스크립트 (Windows)

cd /d "%~dp0"

echo 서버를 백그라운드에서 시작합니다...
start /B uv run app.py > server.log 2>&1

echo 서버가 시작되었습니다.
echo 로그 확인: type server.log
echo 서버 중지: Ctrl+C 또는 작업 관리자에서 python.exe 프로세스 종료
pause

