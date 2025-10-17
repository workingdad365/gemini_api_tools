#!/bin/bash
# Google Gemini API Tools - Web Application 시작 스크립트

# 스크립트 위치로 이동
cd "$(dirname "$0")"

# 환경 변수 로드 (루트의 .env 파일)
if [ -f "../.env" ]; then
    export $(cat ../.env | grep -v '^#' | xargs)
fi

# nohup으로 백그라운드 실행
nohup uv run app.py > server.log 2>&1 &

# PID 저장
echo $! > server.pid

echo "서버가 백그라운드에서 시작되었습니다."
echo "PID: $(cat server.pid)"
echo "로그 확인: tail -f server.log"
echo "서버 중지: ./stop_server.sh"

