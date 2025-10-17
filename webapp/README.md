# Google Gemini API Tools - Web Application

FastAPI 기반 웹 애플리케이션으로 Google Gemini API를 활용한 이미지, 비디오, 음성 생성 도구

## 주요 기능

### 지원 작업
- **Text to Image**: 텍스트 프롬프트로 이미지 생성
- **Image to Image**: 입력 이미지를 기반으로 새로운 이미지 생성
- **Text to Video**: 텍스트 프롬프트로 비디오 생성
- **Image to Video**: 입력 이미지를 비디오로 변환
- **Text to Speech**: 텍스트를 음성으로 변환 (30가지 voice 옵션)

### 웹 UI 기능
- 반응형 디자인 (PC 및 모바일 지원)
- 드래그앤드롭 파일 업로드 (PC)
- 실시간 로그 표시
- 생성된 파일 미리보기 및 다운로드
- 프롬프트 저장/관리

## 설치 및 실행

### uv를 이용한 실행 (권장)

0. uv가 설치되어 있지 않다면
```bash
pip install uv
```

1. **프로젝트 루트의 .env 파일 설정**
```env
GEMINI_API_KEY=your_actual_api_key
```

2. **웹 애플리케이션 실행**
```bash
cd webapp
uv run app.py
```

uv가 자동으로 가상환경 생성 및 의존성 설치를 처리합니다.

3. **접속**
- 로컬: http://localhost:33000
- 외부: http://[서버IP]:33000

### 전통적인 방법

1. **의존성 설치**
```bash
cd webapp
pip install -r requirements.txt
```

2. **환경 변수 설정**

프로젝트 루트의 `.env` 파일에 API 키가 설정되어 있어야 합니다:

```env
GEMINI_API_KEY=your_actual_api_key
```

3. **서버 실행**
```bash
# webapp 디렉토리에서
python app.py
```

또는 uvicorn 직접 실행:
```bash
uvicorn app:app --host 0.0.0.0 --port 33000
```

4. **접속**
- 로컬: http://localhost:33000
- 외부: http://[서버IP]:33000

## 서버 배포 (백그라운드 실행)

### 방법 1: 셸 스크립트 사용 (Linux/Mac)

```bash
# 실행 권한 부여
chmod +x start_server.sh stop_server.sh

# 서버 시작
./start_server.sh

# 로그 확인
tail -f server.log

# 서버 중지
./stop_server.sh
```

### 방법 2: nohup 직접 사용

```bash
cd webapp
nohup uv run app.py > server.log 2>&1 &
echo $! > server.pid

# 서버 중지
kill $(cat server.pid)
```

### 방법 3: systemd 서비스 (권장 - Linux)

1. **서비스 파일 수정**
```bash
# gemini-api-webapp.service 파일에서 다음 항목 수정:
# - YOUR_USERNAME: 실제 사용자명
# - /path/to/gemini_api_tools: 실제 경로
```

2. **서비스 설치**
```bash
sudo cp gemini-api-webapp.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable gemini-api-webapp
sudo systemctl start gemini-api-webapp
```

3. **서비스 관리**
```bash
# 상태 확인
sudo systemctl status gemini-api-webapp

# 로그 확인
sudo journalctl -u gemini-api-webapp -f

# 서비스 중지
sudo systemctl stop gemini-api-webapp

# 서비스 재시작
sudo systemctl restart gemini-api-webapp
```

### 방법 4: screen 또는 tmux 사용

```bash
# screen 사용
screen -S gemini-webapp
cd webapp
uv run app.py
# Ctrl+A, D로 detach

# 다시 접속
screen -r gemini-webapp

# tmux 사용
tmux new -s gemini-webapp
cd webapp
uv run app.py
# Ctrl+B, D로 detach

# 다시 접속
tmux attach -t gemini-webapp
```

### Windows 배포

```cmd
# start_server.bat 실행
start_server.bat
```

또는 Windows 서비스로 등록하려면 NSSM(Non-Sucking Service Manager) 사용 권장

## 디렉토리 구조

```
webapp/
├── app.py                      # FastAPI 백엔드
├── pyproject.toml              # uv 프로젝트 설정
├── requirements.txt            # Python 의존성
├── README.md                   # 문서
├── .gitignore                  # Git 설정
├── start_server.sh             # 서버 시작 스크립트 (Linux/Mac)
├── stop_server.sh              # 서버 중지 스크립트 (Linux/Mac)
├── start_server.bat            # 서버 시작 스크립트 (Windows)
├── gemini-api-webapp.service   # systemd 서비스 파일
├── data.db                     # 프롬프트 데이터베이스 (자동 생성, Git 무시)
├── server.log                  # 서버 로그 (자동 생성, Git 무시)
├── server.pid                  # 프로세스 ID (자동 생성, Git 무시)
├── static/                     # 정적 파일
│   ├── index.html             # 메인 HTML
│   ├── css/
│   │   └── style.css          # 커스텀 CSS
│   └── js/
│       └── main.js            # 클라이언트 JavaScript
├── uploads/                   # 업로드된 파일 임시 저장 (자동 생성)
└── outputs/                   # 생성된 파일 저장 (자동 생성)
```

## API 엔드포인트

### 작업 실행
- `POST /api/text-to-image` - 텍스트로 이미지 생성
- `POST /api/image-to-image` - 이미지로 이미지 생성
- `POST /api/text-to-video` - 텍스트로 비디오 생성
- `POST /api/image-to-video` - 이미지로 비디오 생성
- `POST /api/text-to-speech` - 텍스트로 음성 생성

### 프롬프트 관리
- `GET /api/prompts` - 프롬프트 목록 조회
- `POST /api/prompts` - 프롬프트 저장
- `PUT /api/prompts/{id}` - 프롬프트 수정
- `DELETE /api/prompts/{id}` - 프롬프트 삭제

### 파일 다운로드
- `/outputs/{filename}` - 생성된 파일 다운로드

## 사용 방법

1. 브라우저에서 애플리케이션 접속
2. 작업 유형 선택
3. 필요시 입력 파일 업로드 (드래그앤드롭 또는 파일 선택)
4. 설정 조정 (이미지 비율, 비디오 해상도, 음성 등)
5. 프롬프트 입력
6. 실행 버튼 클릭
7. 결과 확인 및 다운로드

## 기술 스택

### 백엔드
- **FastAPI**: 웹 프레임워크
- **Uvicorn**: ASGI 서버
- **google-generativeai**: Gemini API (Image to Image)
- **google-genai**: Gemini API (기타 작업)
- **Pillow**: 이미지 처리
- **SQLite**: 프롬프트 저장

### 프론트엔드
- **Bootstrap 5**: UI 프레임워크
- **Bootstrap Icons**: 아이콘
- **Vanilla JavaScript**: 클라이언트 로직

## 문제 해결 (Troubleshooting)

### 로그 확인 방법

```bash
# 실시간 로그 확인
tail -f webapp/server.log

# 전체 로그 보기
cat webapp/server.log

# 최근 100줄만 보기
tail -n 100 webapp/server.log
```

### 헬스 체크

서버가 정상적으로 실행 중인지 확인:
```bash
curl http://localhost:33000/health
```

응답 예시:
```json
{
  "status": "healthy",
  "api_key_loaded": true,
  "outputs_dir": "/path/to/webapp/outputs",
  "outputs_dir_exists": true,
  "db_path": "/path/to/webapp/data.db",
  "db_exists": true
}
```

### 일반적인 문제

1. **500 Internal Server Error 발생 시**
   - `server.log` 파일에서 상세한 에러 메시지 확인
   - API 키가 올바르게 로드되었는지 확인: `curl http://localhost:33000/health`
   - 환경 변수 확인: `echo $GEMINI_API_KEY`

2. **API 키 문제**
   ```bash
   # .env 파일 위치 확인
   ls -la ../.env
   
   # .env 파일 내용 확인 (키 값은 숨김)
   cat ../.env | grep GEMINI_API_KEY
   ```

3. **포트 충돌**
   ```bash
   # 33000 포트 사용 중인 프로세스 확인
   lsof -i :33000
   netstat -tlnp | grep 33000
   ```

4. **권한 문제**
   ```bash
   # uploads, outputs 디렉토리 권한 확인
   ls -ld uploads outputs
   
   # 권한 부여
   chmod 755 uploads outputs
   ```

5. **원격 접속 문제**
   - 방화벽에서 33000 포트가 열려있는지 확인
   - 서버 IP 주소 확인: `ip addr` 또는 `ifconfig`
   - 클라이언트에서 연결 테스트: `telnet [서버IP] 33000`

### 상세 로그 활성화

기본적으로 상세 로깅이 활성화되어 있지만, 더 자세한 정보가 필요한 경우:

```python
# app.py 상단의 로깅 레벨 변경
logging.basicConfig(
    level=logging.DEBUG,  # INFO -> DEBUG로 변경
    ...
)
```

## 주의사항

- 비디오 생성은 시간이 오래 걸릴 수 있습니다 (수 분 ~ 수십 분)
- 대용량 파일 업로드 시 타임아웃이 발생할 수 있습니다
- 외부 접속 시 방화벽에서 33000 포트를 열어야 합니다
- HTTPS 설정은 별도로 리버스 프록시(nginx, Caddy 등)를 사용하세요
- 웹앱은 독립적인 SQLite 데이터베이스(`webapp/data.db`)를 사용합니다
  - 데스크톱 GUI 버전의 프롬프트와 별도로 관리됩니다

## 보안

- 프로덕션 환경에서는 인증/인가 시스템 추가 권장
- API 키는 절대 클라이언트에 노출하지 않도록 주의
- 파일 업로드 크기 제한 설정 권장
- CORS 설정을 필요에 맞게 조정하세요

## 라이선스

MIT License

