#!/bin/bash
# Google Gemini API Tools - Web Application 중지 스크립트

# 스크립트 위치로 이동
cd "$(dirname "$0")"

if [ -f "server.pid" ]; then
    PID=$(cat server.pid)
    if ps -p $PID > /dev/null; then
        kill $PID
        echo "서버를 중지했습니다. (PID: $PID)"
        rm server.pid
    else
        echo "서버가 실행 중이지 않습니다."
        rm server.pid
    fi
else
    echo "server.pid 파일을 찾을 수 없습니다."
    echo "수동으로 프로세스를 확인하세요: ps aux | grep app.py"
fi

