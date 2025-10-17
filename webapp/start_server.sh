#!/bin/bash
# Google Gemini API Tools - Web Application 시작 스크립트

# 스크립트 위치로 이동
cd "$(dirname "$0")"

# 환경 변수 로드 (루트의 .env 파일)
if [ -f "../.env" ]; then
    export $(cat ../.env | grep -v '^#' | xargs)
    echo "환경 변수 로드됨: ../.env"
else
    echo "경고: ../.env 파일을 찾을 수 없습니다."
fi

# Python 버퍼링 비활성화 및 로그 레벨 설정
export PYTHONUNBUFFERED=1

# nohup으로 백그라운드 실행 (상세한 로그)
nohup uv run app.py > server.log 2>&1 &

# PID 저장
echo $! > server.pid

echo "서버가 백그라운드에서 시작되었습니다."
echo "PID: $(cat server.pid)"
echo "로그 확인: tail -f server.log"
echo "서버 중지: ./stop_server.sh"
echo ""
echo "잠시 후 로그를 확인하려면:"
echo "  tail -f server.log"

