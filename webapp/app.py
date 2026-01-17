import os
import time
import sqlite3
import mimetypes
import struct
import logging
import traceback
import random
import uuid
from datetime import datetime
from io import BytesIO
from typing import Optional
from pathlib import Path

import secrets
import hashlib
from collections import defaultdict

from fastapi import FastAPI, File, UploadFile, Form, HTTPException, Request, Response, Cookie, Depends
from fastapi.responses import FileResponse, JSONResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from google import genai
from google.genai import types
from PIL import Image
from dotenv import load_dotenv

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('server.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# 디렉토리 설정
BASE_DIR = Path(__file__).resolve().parent

# 환경 변수 로드
load_dotenv()

# webapp 디렉토리의 .env 파일 로드
webapp_env_path = BASE_DIR / ".env"
if webapp_env_path.exists():
    load_dotenv(webapp_env_path)
    logger.info(f"Loaded .env from {webapp_env_path}")
else:
    logger.warning(f".env file not found at {webapp_env_path}")

# 루트 디렉토리의 .env 파일도 로드 (fallback)
root_env_path = BASE_DIR.parent / ".env"
if root_env_path.exists():
    load_dotenv(root_env_path)
    logger.info(f"Loaded .env from {root_env_path}")

app = FastAPI(title="Google Gemini API Tools")

# CORS 설정
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 디렉토리 설정
STATIC_DIR = BASE_DIR / "static"
UPLOADS_DIR = BASE_DIR / "uploads"
OUTPUTS_DIR = BASE_DIR / "outputs"
DB_PATH = BASE_DIR / "data.db"  # 웹앱 전용 데이터베이스

# 디렉토리 생성
UPLOADS_DIR.mkdir(exist_ok=True)
OUTPUTS_DIR.mkdir(exist_ok=True)

# API 키 리스트 초기화
# GEMINI_API_KEY_LIST에서 키 목록 로드
api_key_list_str = os.getenv("GEMINI_API_KEY_LIST")
if api_key_list_str:
    # 공백으로 분리하여 리스트로 변환
    api_key_list = api_key_list_str.split()
    if not api_key_list:
        logger.error("GEMINI_API_KEY_LIST is empty")
        raise ValueError("GEMINI_API_KEY_LIST is empty")
    logger.info(f"Loaded {len(api_key_list)} API keys from GEMINI_API_KEY_LIST")
else:
    # fallback: GEMINI_API_KEY 사용
    single_api_key = os.getenv("GEMINI_API_KEY")
    if not single_api_key:
        logger.error("GEMINI_API_KEY or GEMINI_API_KEY_LIST not found in environment variables")
        raise ValueError("GEMINI_API_KEY or GEMINI_API_KEY_LIST not found in environment variables")
    api_key_list = [single_api_key]
    logger.info("Using single GEMINI_API_KEY")

logger.info("API keys loaded successfully")

def get_genai_client() -> genai.Client:
    """매 요청마다 랜덤 API 키를 선택하여 새 클라이언트 생성"""
    selected_key = random.choice(api_key_list)
    # 키의 앞 8자만 표시 (보안)
    masked_key = selected_key[:8] + "..." if len(selected_key) > 8 else selected_key
    logger.info(f"Selected API key: {masked_key} (from {len(api_key_list)} keys)")
    return genai.Client(api_key=selected_key)

# 비디오 객체 저장소 (메모리)
# UUID -> generated_video 객체 매핑
video_objects_cache = {}

# 이미지 채팅 세션 저장소 (메모리)
# session_id -> {"chat": chat, "client": client} 매핑
image_chat_sessions = {}

# 로그인 설정
LOGIN_ID = os.getenv("LOGIN_ID", "admin")
LOGIN_PASSWORD = os.getenv("LOGIN_PASSWORD", "admin")

# 세션 저장소 (메모리)
# session_token -> {"ip": str, "created_at": float}
active_sessions = {}

# IP 블록 관리 (메모리)
# ip -> [timestamp1, timestamp2, ...] (실패한 시간 기록)
failed_login_attempts = defaultdict(list)
blocked_ips = {}  # ip -> block_until_timestamp
BLOCK_DURATION = 300  # 5분 블록
MAX_FAILED_ATTEMPTS = 3  # 1분 내 3번 실패 시 블록
ATTEMPT_WINDOW = 60  # 1분

# 공통 안전 필터 설정 (OFF)
SAFETY_SETTINGS = [
    types.SafetySetting(
        category=types.HarmCategory.HARM_CATEGORY_HARASSMENT,
        threshold=types.HarmBlockThreshold.OFF,
    ),
    types.SafetySetting(
        category=types.HarmCategory.HARM_CATEGORY_HATE_SPEECH,
        threshold=types.HarmBlockThreshold.OFF,
    ),
    types.SafetySetting(
        category=types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
        threshold=types.HarmBlockThreshold.OFF,
    ),
    types.SafetySetting(
        category=types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
        threshold=types.HarmBlockThreshold.OFF,
    ),
]

# 정적 파일 제공
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
app.mount("/outputs", StaticFiles(directory=str(OUTPUTS_DIR)), name="outputs")

# 데이터베이스 초기화
def init_database():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS prompt (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

init_database()

# Pydantic 모델
class PromptCreate(BaseModel):
    content: str

class PromptUpdate(BaseModel):
    id: int
    content: str

class TaskStatus(BaseModel):
    status: str
    message: str
    output_file: Optional[str] = None

# 유틸리티 함수
def pil_to_bytes(pil_image: Image.Image, image_format: str = 'JPEG') -> bytes:
    """PIL Image를 bytes로 변환"""
    img_byte_arr = BytesIO()
    pil_image.save(img_byte_arr, format=image_format)
    return img_byte_arr.getvalue()

def convert_to_wav(audio_data: bytes, mime_type: str) -> bytes:
    """오디오 데이터를 WAV 포맷으로 변환"""
    parameters = parse_audio_mime_type(mime_type)
    bits_per_sample = parameters["bits_per_sample"]
    sample_rate = parameters["rate"]
    num_channels = 1
    data_size = len(audio_data)
    bytes_per_sample = bits_per_sample // 8
    block_align = num_channels * bytes_per_sample
    byte_rate = sample_rate * block_align
    chunk_size = 36 + data_size
    
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        chunk_size,
        b"WAVE",
        b"fmt ",
        16,
        1,
        num_channels,
        sample_rate,
        byte_rate,
        block_align,
        bits_per_sample,
        b"data",
        data_size
    )
    return header + audio_data

def parse_audio_mime_type(mime_type: str) -> dict:
    """오디오 MIME 타입 파싱"""
    bits_per_sample = 16
    rate = 24000
    
    parts = mime_type.split(";")
    for param in parts:
        param = param.strip()
        if param.lower().startswith("rate="):
            try:
                rate_str = param.split("=", 1)[1]
                rate = int(rate_str)
            except (ValueError, IndexError):
                pass
        elif param.startswith("audio/L"):
            try:
                bits_per_sample = int(param.split("L", 1)[1])
            except (ValueError, IndexError):
                pass
    
    return {"bits_per_sample": bits_per_sample, "rate": rate}

def build_image_config(aspect_ratio: Optional[str] = None, resolution: Optional[str] = None) -> types.ImageConfig:
    """이미지 설정 생성"""
    def prune(values: dict) -> dict:
        return {key: value for key, value in values.items() if value}

    candidates = [
        prune({"aspect_ratio": aspect_ratio, "image_size": resolution}),
        prune({"aspectRatio": aspect_ratio, "imageSize": resolution}),
    ]

    for candidate in candidates:
        if not candidate:
            continue
        try:
            return types.ImageConfig(**candidate)
        except Exception:
            continue

    if not any(candidates):
        return types.ImageConfig()

    logger.warning("ImageConfig does not support image size or aspect ratio on this SDK version")
    return types.ImageConfig()

def build_user_parts_for_images(upload_paths: list[Path], prompt: str) -> list[types.Part]:
    """이미지와 프롬프트로 사용자 파트 생성"""
    parts = []
    for upload_path in upload_paths:
        pil_image = Image.open(upload_path)
        mime_type = mimetypes.guess_type(upload_path)[0] or "image/png"
        img_format = mime_type.split('/')[-1].upper()
        if img_format == 'JPG':
            img_format = 'JPEG'
        image_bytes = pil_to_bytes(pil_image, image_format=img_format)
        parts.append(types.Part.from_bytes(data=image_bytes, mime_type=mime_type))
    parts.append(types.Part.from_text(text=prompt))
    return parts

def get_client_ip(request: Request) -> str:
    """클라이언트 IP 주소 추출"""
    # X-Forwarded-For 헤더 확인 (프록시 뒤에 있는 경우)
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    # X-Real-IP 헤더 확인
    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        return real_ip
    # 직접 연결인 경우
    return request.client.host if request.client else "unknown"

def is_ip_blocked(ip: str) -> bool:
    """IP가 블록되어 있는지 확인"""
    if ip in blocked_ips:
        if time.time() < blocked_ips[ip]:
            return True
        else:
            # 블록 시간 지남 -> 해제
            del blocked_ips[ip]
    return False

def record_failed_attempt(ip: str) -> bool:
    """실패한 로그인 시도 기록. 블록해야 하면 True 반환"""
    current_time = time.time()
    # 1분 이내의 시도만 유지
    failed_login_attempts[ip] = [
        t for t in failed_login_attempts[ip] 
        if current_time - t < ATTEMPT_WINDOW
    ]
    failed_login_attempts[ip].append(current_time)
    
    if len(failed_login_attempts[ip]) >= MAX_FAILED_ATTEMPTS:
        # IP 블록
        blocked_ips[ip] = current_time + BLOCK_DURATION
        failed_login_attempts[ip] = []
        logger.warning(f"IP blocked due to too many failed attempts: {ip}")
        return True
    return False

def verify_session(session_token: str = Cookie(None)) -> bool:
    """세션 토큰 검증"""
    if not session_token:
        return False
    return session_token in active_sessions

async def require_auth(request: Request, session_token: str = Cookie(None)):
    """인증 필요한 엔드포인트용 의존성"""
    if not verify_session(session_token):
        raise HTTPException(status_code=401, detail="Unauthorized")
    return True

# API 엔드포인트
@app.get("/login")
async def login_page(request: Request, session_token: str = Cookie(None)):
    """로그인 페이지"""
    # 이미 로그인되어 있으면 메인 페이지로 리다이렉트
    if verify_session(session_token):
        return RedirectResponse(url="/", status_code=302)
    
    client_ip = get_client_ip(request)
    blocked = is_ip_blocked(client_ip)
    
    login_html = f'''
    <!DOCTYPE html>
    <html lang="ko">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Login - Google Gemini API Tools</title>
        <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
        <link href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.0/font/bootstrap-icons.css" rel="stylesheet">
        <style>
            body {{
                background: linear-gradient(135deg, #1a1a2e 0%, #16213e 50%, #0f3460 100%);
                min-height: 100vh;
                display: flex;
                align-items: center;
                justify-content: center;
            }}
            .login-card {{
                background: rgba(255, 255, 255, 0.95);
                border-radius: 16px;
                padding: 2.5rem;
                box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
                max-width: 400px;
                width: 100%;
            }}
            .login-title {{
                color: #1a1a2e;
                font-weight: 700;
                margin-bottom: 1.5rem;
            }}
            .form-control:focus {{
                border-color: #4361ee;
                box-shadow: 0 0 0 0.2rem rgba(67, 97, 238, 0.25);
            }}
            .btn-login {{
                background: linear-gradient(135deg, #4361ee, #3a0ca3);
                border: none;
                padding: 0.75rem;
                font-weight: 600;
            }}
            .btn-login:hover {{
                background: linear-gradient(135deg, #3a0ca3, #4361ee);
            }}
        </style>
    </head>
    <body>
        <div class="login-card">
            <h3 class="login-title text-center">
                <i class="bi bi-stars text-primary"></i> Gemini API Tools
            </h3>
            {"<div class='alert alert-danger'>IP가 일시적으로 차단되었습니다. 잠시 후 다시 시도하세요.</div>" if blocked else ""}
            <form method="post" action="/login" {"style='display:none;'" if blocked else ""}>
                <div class="mb-3">
                    <label class="form-label">ID</label>
                    <input type="text" class="form-control" name="login_id" required autofocus>
                </div>
                <div class="mb-3">
                    <label class="form-label">Password</label>
                    <input type="password" class="form-control" name="login_password" required>
                </div>
                <button type="submit" class="btn btn-primary btn-login w-100">
                    <i class="bi bi-box-arrow-in-right"></i> 로그인
                </button>
            </form>
        </div>
    </body>
    </html>
    '''
    return HTMLResponse(content=login_html)

@app.post("/login")
async def login_submit(
    request: Request,
    response: Response,
    login_id: str = Form(...),
    login_password: str = Form(...)
):
    """로그인 처리"""
    client_ip = get_client_ip(request)
    
    # IP 블록 확인
    if is_ip_blocked(client_ip):
        logger.warning(f"Blocked IP attempted login: {client_ip}")
        return RedirectResponse(url="/login?error=blocked", status_code=302)
    
    # 인증 확인
    if login_id == LOGIN_ID and login_password == LOGIN_PASSWORD:
        # 로그인 성공
        session_token = secrets.token_urlsafe(32)
        active_sessions[session_token] = {
            "ip": client_ip,
            "created_at": time.time()
        }
        logger.info(f"Login successful from IP: {client_ip}")
        
        # 실패 기록 초기화
        if client_ip in failed_login_attempts:
            del failed_login_attempts[client_ip]
        
        redirect_response = RedirectResponse(url="/", status_code=302)
        redirect_response.set_cookie(
            key="session_token",
            value=session_token,
            httponly=True,
            max_age=86400,  # 24시간
            samesite="lax"
        )
        return redirect_response
    else:
        # 로그인 실패
        logger.warning(f"Login failed from IP: {client_ip}")
        is_blocked = record_failed_attempt(client_ip)
        
        if is_blocked:
            return RedirectResponse(url="/login?error=blocked", status_code=302)
        else:
            # 실패 메시지와 함께 로그인 페이지 반환
            remaining = MAX_FAILED_ATTEMPTS - len(failed_login_attempts.get(client_ip, []))
            error_html = f'''
            <!DOCTYPE html>
            <html lang="ko">
            <head>
                <meta charset="UTF-8">
                <meta name="viewport" content="width=device-width, initial-scale=1.0">
                <title>Login - Google Gemini API Tools</title>
                <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
                <link href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.0/font/bootstrap-icons.css" rel="stylesheet">
                <style>
                    body {{
                        background: linear-gradient(135deg, #1a1a2e 0%, #16213e 50%, #0f3460 100%);
                        min-height: 100vh;
                        display: flex;
                        align-items: center;
                        justify-content: center;
                    }}
                    .login-card {{
                        background: rgba(255, 255, 255, 0.95);
                        border-radius: 16px;
                        padding: 2.5rem;
                        box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
                        max-width: 400px;
                        width: 100%;
                    }}
                    .login-title {{
                        color: #1a1a2e;
                        font-weight: 700;
                        margin-bottom: 1.5rem;
                    }}
                    .form-control:focus {{
                        border-color: #4361ee;
                        box-shadow: 0 0 0 0.2rem rgba(67, 97, 238, 0.25);
                    }}
                    .btn-login {{
                        background: linear-gradient(135deg, #4361ee, #3a0ca3);
                        border: none;
                        padding: 0.75rem;
                        font-weight: 600;
                    }}
                    .btn-login:hover {{
                        background: linear-gradient(135deg, #3a0ca3, #4361ee);
                    }}
                </style>
            </head>
            <body>
                <div class="login-card">
                    <h3 class="login-title text-center">
                        <i class="bi bi-stars text-primary"></i> Gemini API Tools
                    </h3>
                    <div class="alert alert-warning">
                        ID 또는 비밀번호가 올바르지 않습니다. (남은 시도: {remaining}회)
                    </div>
                    <form method="post" action="/login">
                        <div class="mb-3">
                            <label class="form-label">ID</label>
                            <input type="text" class="form-control" name="login_id" required autofocus>
                        </div>
                        <div class="mb-3">
                            <label class="form-label">Password</label>
                            <input type="password" class="form-control" name="login_password" required>
                        </div>
                        <button type="submit" class="btn btn-primary btn-login w-100">
                            <i class="bi bi-box-arrow-in-right"></i> 로그인
                        </button>
                    </form>
                </div>
            </body>
            </html>
            '''
            return HTMLResponse(content=error_html)

@app.get("/logout")
async def logout(response: Response, session_token: str = Cookie(None)):
    """로그아웃"""
    if session_token and session_token in active_sessions:
        del active_sessions[session_token]
    
    redirect_response = RedirectResponse(url="/login", status_code=302)
    redirect_response.delete_cookie(key="session_token")
    return redirect_response

@app.get("/")
async def read_root(request: Request, session_token: str = Cookie(None)):
    """메인 페이지 (인증 필요)"""
    if not verify_session(session_token):
        return RedirectResponse(url="/login", status_code=302)
    return FileResponse(str(STATIC_DIR / "index.html"))

@app.get("/health")
async def health_check():
    """헬스 체크 및 환경 정보"""
    return JSONResponse({
        "status": "healthy",
        "api_key_loaded": bool(os.getenv("GEMINI_API_KEY")),
        "outputs_dir": str(OUTPUTS_DIR),
        "outputs_dir_exists": OUTPUTS_DIR.exists(),
        "db_path": str(DB_PATH),
        "db_exists": DB_PATH.exists()
    })

@app.post("/api/text-to-image")
async def text_to_image(
    prompt: str = Form(...),
    aspect_ratio: str = Form("16:9"),
    model: str = Form("gemini-2.5-flash-image"),
    resolution: str = Form("2K"),
    is_new: bool = Form(True),
    session_id: Optional[str] = Form(None)
):
    """Text to Image 작업 (Multi-turn 지원)"""
    try:
        logger.info(f"Text to Image request - prompt length: {len(prompt)}, aspect_ratio: {aspect_ratio}, model: {model}, resolution: {resolution}, is_new: {is_new}, session_id: {session_id}")
        logger.info(f"Text to Image prompt: {prompt}")
        
        client = get_genai_client()
        current_session_id = session_id
        
        # chat 생성용 config (response_modalities만 설정)
        chat_config = types.GenerateContentConfig(
            safety_settings=SAFETY_SETTINGS,
            response_modalities=["TEXT", "IMAGE"],
        )
        
        # send_message용 config (image_config 포함)
        message_config = types.GenerateContentConfig(
            image_config=build_image_config(aspect_ratio=aspect_ratio, resolution=resolution),
        )
        
        logger.info("Calling Gemini API...")
        text_response = ""  # 텍스트 응답 누적
        
        # Multi-turn 모드: 채팅 세션 사용
        if not is_new and session_id and session_id in image_chat_sessions:
            # 기존 세션 사용
            chat = image_chat_sessions[session_id]["chat"]
            logger.info(f"Using existing chat session: {session_id}")
            
            # 메시지 전송 (message_config 포함)
            response = chat.send_message(prompt, config=message_config)
        else:
            # 새 이미지 생성은 generate_content로 처리
            response = client.models.generate_content(
                model=model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_modalities=["TEXT", "IMAGE"],
                    image_config=build_image_config(aspect_ratio=aspect_ratio, resolution=resolution),
                ),
            )
            
            # 새 세션 생성 (history 포함)
            current_session_id = str(uuid.uuid4())
            history = [
                types.Content(
                    role="user",
                    parts=[types.Part.from_text(text=prompt)],
                ),
                types.Content(
                    role="model",
                    parts=response.parts,
                ),
            ]
            chat = client.chats.create(
                model=model,
                config=chat_config,
                history=history,
            )
            image_chat_sessions[current_session_id] = {"chat": chat, "client": client}
            logger.info(f"Created new chat session: {current_session_id}")
        
        # 응답 처리
        if response is None or response.parts is None:
            logger.error("Empty response parts from Gemini API")
            raise HTTPException(status_code=500, detail="응답 데이터 없음")

        for part in response.parts:
            if part.text is not None:
                text_response += part.text
                logger.info(f"Received text response: {part.text}")
            # as_image() 방식 우선 시도
            if hasattr(part, 'as_image'):
                image = part.as_image()
                if image:
                    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
                    output_filename = f"output_{timestamp}.png"
                    output_path = OUTPUTS_DIR / output_filename
                    image.save(str(output_path))
                    
                    logger.info(f"Image saved successfully: {output_filename}")
                    response_data = {
                        "status": "success",
                        "message": "이미지가 생성되었습니다.",
                        "output_file": f"/outputs/{output_filename}",
                        "session_id": current_session_id
                    }
                    if text_response:
                        response_data["llm_response"] = text_response
                    
                    return JSONResponse(response_data)
            # inline_data 방식도 지원
            elif hasattr(part, 'inline_data') and part.inline_data is not None and part.inline_data.data:
                timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
                inline_data = part.inline_data
                data_buffer = inline_data.data
                file_extension = mimetypes.guess_extension(inline_data.mime_type)
                
                output_filename = f"output_{timestamp}{file_extension}"
                output_path = OUTPUTS_DIR / output_filename
                
                with open(output_path, "wb") as f:
                    f.write(data_buffer)
                
                logger.info(f"Image saved successfully: {output_filename}")
                response_data = {
                    "status": "success",
                    "message": "이미지가 생성되었습니다.",
                    "output_file": f"/outputs/{output_filename}",
                    "session_id": current_session_id
                }
                if text_response:
                    response_data["llm_response"] = text_response
                
                return JSONResponse(response_data)
        
        # 이미지가 없지만 텍스트 응답이 있는 경우 (콘티, 설명 등)
        if text_response:
            logger.info("No image generated, but text response received")
            return JSONResponse({
                "status": "success",
                "message": "텍스트 응답을 받았습니다.",
                "text_only": True,
                "llm_response": text_response,
                "session_id": current_session_id
            })
        
        logger.error("No image or text data received from API")
        raise HTTPException(status_code=500, detail="응답 데이터 없음")
    
    except Exception as e:
        logger.error(f"Text to Image error: {str(e)}")
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/image-to-image")
async def image_to_image(
    prompt: str = Form(...),
    files: list[UploadFile] = File(None),
    model: str = Form("gemini-2.5-flash-image"),
    resolution: str = Form("2K"),
    is_new: bool = Form(True),
    session_id: Optional[str] = Form(None)
):
    """Image to Image 작업 (멀티 이미지 지원, Multi-turn 지원)"""
    upload_paths = []
    try:
        logger.info(f"Image to Image request - model: {model}, resolution: {resolution}, is_new: {is_new}, session_id: {session_id}")
        logger.info(f"Image to Image prompt: {prompt}")
        
        client = get_genai_client()
        current_session_id = session_id
        text_response = ""  # 텍스트 응답 누적
        
        # chat 생성용 config (response_modalities만 설정)
        chat_config = types.GenerateContentConfig(
            safety_settings=SAFETY_SETTINGS,
            response_modalities=["TEXT", "IMAGE"],
        )
        
        # send_message용 config (image_config 포함)
        message_config = types.GenerateContentConfig(
            image_config=build_image_config(resolution=resolution),
        )
        
        # Multi-turn 모드: 기존 세션 사용 (프롬프트만 전송)
        if not is_new and session_id and session_id in image_chat_sessions:
            chat = image_chat_sessions[session_id]["chat"]
            logger.info(f"Using existing chat session for image editing: {session_id}")
            
            # 메시지 전송 (message_config 포함)
            response = chat.send_message(prompt, config=message_config)
        
        # 새 세션 생성 모드: 이미지와 프롬프트 함께 전송
        else:
            if not files:
                raise HTTPException(status_code=400, detail="새로 만들기 모드에서는 이미지 파일이 필요합니다.")
            
            # 모델에 따라 최대 파일 수 결정
            max_files = 14 if model == "gemini-3-pro-image-preview" else 3
            files_to_process = files[:max_files]
            logger.info(f"Processing {len(files_to_process)} images for image-to-image with model {model}")
            
            # 파일 저장
            for file in files_to_process:
                upload_path = UPLOADS_DIR / file.filename
                with open(upload_path, "wb") as buffer:
                    buffer.write(await file.read())
                upload_paths.append(upload_path)
            
            # 이미지 로드
            images = [Image.open(path) for path in upload_paths]
            
            # 새 이미지 생성은 generate_content로 처리
            response = client.models.generate_content(
                model=model,
                contents=images + [prompt],
                config=types.GenerateContentConfig(
                    response_modalities=["TEXT", "IMAGE"],
                    image_config=build_image_config(resolution=resolution),
                ),
            )
            
            # 새 세션 생성 (history 포함)
            current_session_id = str(uuid.uuid4())
            history = [
                types.Content(
                    role="user",
                    parts=build_user_parts_for_images(upload_paths, prompt),
                ),
                types.Content(
                    role="model",
                    parts=response.parts,
                ),
            ]
            chat = client.chats.create(
                model=model,
                config=chat_config,
                history=history,
            )
            image_chat_sessions[current_session_id] = {"chat": chat, "client": client}
            logger.info(f"Created new chat session for image-to-image: {current_session_id}")
            
            # 업로드된 파일 삭제
            for upload_path in upload_paths:
                if upload_path.exists():
                    upload_path.unlink()
            upload_paths = []
        
        # 응답 처리 (공통)
        if response is None or response.parts is None:
            logger.error("Empty response parts from Gemini API")
            raise HTTPException(status_code=500, detail="응답 데이터 없음")

        for part in response.parts:
            if part.text is not None:
                text_response += part.text
                logger.info(f"Received text response: {part.text}")
            # as_image() 방식 우선 시도
            if hasattr(part, 'as_image'):
                image = part.as_image()
                if image:
                    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
                    output_filename = f"output_{timestamp}.png"
                    output_path = OUTPUTS_DIR / output_filename
                    image.save(str(output_path))
                    
                    response_data = {
                        "status": "success",
                        "message": "이미지가 생성되었습니다.",
                        "output_file": f"/outputs/{output_filename}",
                        "session_id": current_session_id
                    }
                    if text_response:
                        response_data["llm_response"] = text_response
                    
                    return JSONResponse(response_data)
            # inline_data 방식도 지원
            elif hasattr(part, 'inline_data') and part.inline_data is not None and part.inline_data.data:
                image_data = BytesIO(part.inline_data.data)
                img = Image.open(image_data)
                
                timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
                output_filename = f"output_{timestamp}.png"
                output_path = OUTPUTS_DIR / output_filename
                
                img.save(output_path)
                
                response_data = {
                    "status": "success",
                    "message": "이미지가 생성되었습니다.",
                    "output_file": f"/outputs/{output_filename}",
                    "session_id": current_session_id
                }
                if text_response:
                    response_data["llm_response"] = text_response
                
                return JSONResponse(response_data)
        
        # 이미지 데이터가 없지만 텍스트 응답이 있는 경우
        if text_response:
            logger.info("Text-only response received (no image generated)")
            return JSONResponse({
                "status": "success",
                "message": "텍스트 응답을 받았습니다.",
                "llm_response": text_response,
                "text_only": True,
                "session_id": current_session_id
            })
        
        logger.error("No image data or text response received from API")
        raise HTTPException(status_code=500, detail="이미지 생성 실패: 응답에 이미지 데이터가 없습니다.")
    
    except Exception as e:
        logger.error(f"Image to Image error: {str(e)}")
        logger.error(traceback.format_exc())
        for upload_path in upload_paths:
            if upload_path.exists():
                upload_path.unlink()
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/text-to-video")
async def text_to_video(
    prompt: str = Form(...),
    resolution: str = Form("720p"),
    aspect_ratio: str = Form("16:9")
):
    """Text to Video 작업"""
    try:
        model = "veo-3.1-generate-preview"
        client = get_genai_client()
        operation = client.models.generate_videos(
            model=model,
            prompt=prompt,
            config=types.GenerateVideosConfig(
                resolution=resolution,
                aspect_ratio=aspect_ratio
            )
        )
        
        # 작업 완료 대기
        while not operation.done:
            time.sleep(10)
            operation = client.operations.get(operation)
        
        # 작업 결과 확인
        if hasattr(operation, 'error') and operation.error:
            error_msg = f"Video generation failed: {operation.error}"
            logger.error(error_msg)
            raise HTTPException(status_code=500, detail=error_msg)
        
        if not operation.response or not operation.response.generated_videos:
            # RAI 필터링 이유 확인
            error_detail = "비디오 생성 실패"
            if operation.response and hasattr(operation.response, 'rai_media_filtered_reasons'):
                filtered_reasons = operation.response.rai_media_filtered_reasons
                if filtered_reasons:
                    reasons_text = "\n".join(filtered_reasons)
                    error_detail = f"비디오 생성 실패:\n{reasons_text}"
                    logger.error(f"No videos generated. Filtered reasons: {filtered_reasons}")
                else:
                    error_detail = "비디오 생성 실패: 응답에 비디오가 없습니다."
                    logger.error(f"No videos generated. Operation response: {operation.response}")
            else:
                error_detail = "비디오 생성 실패: 응답에 비디오가 없습니다."
                logger.error(f"No videos generated. Operation response: {operation.response}")
            raise HTTPException(status_code=500, detail=error_detail)
        
        if len(operation.response.generated_videos) == 0:
            logger.error("Generated videos list is empty")
            raise HTTPException(status_code=500, detail="비디오 생성 실패: 생성된 비디오가 없습니다.")
        
        # 비디오 다운로드
        generated_video = operation.response.generated_videos[0]
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        output_filename = f"output_{timestamp}.mp4"
        output_path = OUTPUTS_DIR / output_filename
        
        client.files.download(file=generated_video.video)
        generated_video.video.save(str(output_path))
        
        # 비디오 객체를 메모리에 저장 (확장 기능용)
        video_uuid = str(uuid.uuid4())
        video_objects_cache[video_uuid] = generated_video
        logger.info(f"Saved video object with UUID: {video_uuid}")
        
        return JSONResponse({
            "status": "success",
            "message": "비디오가 생성되었습니다.",
            "output_file": f"/outputs/{output_filename}",
            "video_uuid": video_uuid
        })
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/image-to-video")
async def image_to_video(
    prompt: str = Form(...),
    files: list[UploadFile] = File(...),
    resolution: str = Form("720p"),
    aspect_ratio: str = Form("16:9")
):
    """Image to Video 작업 (멀티 이미지 지원)"""
    upload_paths = []
    try:
        # 최대 3개까지만 처리
        files_to_process = files[:3]
        logger.info(f"Processing {len(files_to_process)} images for image-to-video")
        
        # 파일 저장
        for file in files_to_process:
            upload_path = UPLOADS_DIR / file.filename
            with open(upload_path, "wb") as buffer:
                buffer.write(await file.read())
            upload_paths.append(upload_path)
        
        model = "veo-3.1-generate-preview"
        client = get_genai_client()
        
        # 프롬프트가 없으면 기본 프롬프트 사용
        if not prompt:
            prompt = "Animate this image"
        
        # 1개 이미지인 경우 기존 방식 사용 (image 파라미터)
        if len(upload_paths) == 1:
            pil_image = Image.open(upload_paths[0])
            
            # MIME 타입 추론
            mime_type = mimetypes.guess_type(upload_paths[0])[0]
            if not mime_type:
                mime_type = "image/png"
            
            # 이미지를 바이트로 변환
            img_format = mime_type.split('/')[-1].upper()
            if img_format == 'JPG':
                img_format = 'JPEG'
            image_bytes = pil_to_bytes(pil_image, image_format=img_format)
            
            # types.Image 객체 생성
            safe_image = types.Image(
                image_bytes=image_bytes,
                mime_type=mime_type
            )
            
            operation = client.models.generate_videos(
                model=model,
                prompt=prompt,
                image=safe_image,
                config=types.GenerateVideosConfig(
                    resolution=resolution,
                    aspect_ratio=aspect_ratio
                )
            )
        else:
            # 2개 이상 이미지인 경우 reference_images 사용
            reference_images = []
            for upload_path in upload_paths:
                pil_image = Image.open(upload_path)
                
                # MIME 타입 추론
                mime_type = mimetypes.guess_type(upload_path)[0]
                if not mime_type:
                    mime_type = "image/jpeg"
                
                # 이미지를 바이트로 변환
                img_format = mime_type.split('/')[-1].upper()
                if img_format == 'JPG':
                    img_format = 'JPEG'
                image_bytes = pil_to_bytes(pil_image, image_format=img_format)
                
                # VideoGenerationReferenceImage 생성
                reference_image = types.VideoGenerationReferenceImage(
                    image=types.Image(image_bytes=image_bytes, mime_type=mime_type),
                    reference_type="asset"
                )
                reference_images.append(reference_image)
            
            operation = client.models.generate_videos(
                model=model,
                prompt=prompt,
                config=types.GenerateVideosConfig(
                    reference_images=reference_images,
                    resolution=resolution,
                    aspect_ratio=aspect_ratio
                )
            )
        
        # 작업 완료 대기
        while not operation.done:
            time.sleep(10)
            operation = client.operations.get(operation)
        
        # 작업 결과 확인
        if hasattr(operation, 'error') and operation.error:
            error_msg = f"Video generation failed: {operation.error}"
            logger.error(error_msg)
            raise HTTPException(status_code=500, detail=error_msg)
        
        if not operation.response or not operation.response.generated_videos:
            # RAI 필터링 이유 확인
            error_detail = "비디오 생성 실패"
            if operation.response and hasattr(operation.response, 'rai_media_filtered_reasons'):
                filtered_reasons = operation.response.rai_media_filtered_reasons
                if filtered_reasons:
                    reasons_text = "\n".join(filtered_reasons)
                    error_detail = f"비디오 생성 실패:\n{reasons_text}"
                    logger.error(f"No videos generated. Filtered reasons: {filtered_reasons}")
                else:
                    error_detail = "비디오 생성 실패: 응답에 비디오가 없습니다."
                    logger.error(f"No videos generated. Operation response: {operation.response}")
            else:
                error_detail = "비디오 생성 실패: 응답에 비디오가 없습니다."
                logger.error(f"No videos generated. Operation response: {operation.response}")
            raise HTTPException(status_code=500, detail=error_detail)
        
        if len(operation.response.generated_videos) == 0:
            logger.error("Generated videos list is empty")
            raise HTTPException(status_code=500, detail="비디오 생성 실패: 생성된 비디오가 없습니다.")
        
        # 비디오 다운로드
        video = operation.response.generated_videos[0]
        client.files.download(file=video.video)
        
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        output_filename = f"output_{timestamp}.mp4"
        output_path = OUTPUTS_DIR / output_filename
        
        video.video.save(str(output_path))
        
        # 비디오 객체를 메모리에 저장 (확장 기능용)
        video_uuid = str(uuid.uuid4())
        video_objects_cache[video_uuid] = video
        logger.info(f"Saved video object with UUID: {video_uuid}")
        
        # 업로드된 파일 삭제
        for upload_path in upload_paths:
            if upload_path.exists():
                upload_path.unlink()
        
        return JSONResponse({
            "status": "success",
            "message": "비디오가 생성되었습니다.",
            "output_file": f"/outputs/{output_filename}",
            "video_uuid": video_uuid
        })
    
    except Exception as e:
        logger.error(f"Image to Video error: {str(e)}")
        for upload_path in upload_paths:
            if upload_path.exists():
                upload_path.unlink()
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/extend-video")
async def extend_video(
    prompt: str = Form(...),
    video_uuid: str = Form(...),
    resolution: str = Form("720p"),
    aspect_ratio: str = Form("16:9")
):
    """비디오 확장 작업"""
    try:
        logger.info(f"Video extension request - prompt length: {len(prompt)}, video_uuid: {video_uuid}, resolution: {resolution}, aspect_ratio: {aspect_ratio}")
        
        # 메모리에서 비디오 객체 가져오기
        if video_uuid not in video_objects_cache:
            logger.error(f"Video UUID not found in cache: {video_uuid}")
            raise HTTPException(status_code=400, detail=f"비디오를 찾을 수 없습니다. UUID: {video_uuid}")
        
        previous_video = video_objects_cache[video_uuid]
        logger.info(f"Retrieved video object from cache: {video_uuid}")
        
        model = "veo-3.1-generate-preview"
        client = get_genai_client()
        
        # 비디오 확장 작업 시작 (previous_video.video 전달)
        operation = client.models.generate_videos(
            model=model,
            prompt=prompt,
            video=previous_video.video,
            config=types.GenerateVideosConfig(
                number_of_videos=1,
                resolution=resolution,
                aspect_ratio=aspect_ratio
            )
        )
        
        logger.info("Video extension operation started")
        
        # 작업 완료 대기
        while not operation.done:
            time.sleep(10)
            operation = client.operations.get(operation)
            logger.info("Waiting for video extension to complete...")
        
        # 작업 결과 확인
        if hasattr(operation, 'error') and operation.error:
            error_msg = f"Video extension failed: {operation.error}"
            logger.error(error_msg)
            raise HTTPException(status_code=500, detail=error_msg)
        
        if not operation.response or not operation.response.generated_videos:
            # RAI 필터링 이유 확인
            error_detail = "비디오 확장 실패"
            if operation.response and hasattr(operation.response, 'rai_media_filtered_reasons'):
                filtered_reasons = operation.response.rai_media_filtered_reasons
                if filtered_reasons:
                    reasons_text = "\n".join(filtered_reasons)
                    error_detail = f"비디오 확장 실패:\n{reasons_text}"
                    logger.error(f"No videos generated. Filtered reasons: {filtered_reasons}")
                else:
                    error_detail = "비디오 확장 실패: 응답에 비디오가 없습니다."
                    logger.error(f"No videos generated. Operation response: {operation.response}")
            else:
                error_detail = "비디오 확장 실패: 응답에 비디오가 없습니다."
                logger.error(f"No videos generated. Operation response: {operation.response}")
            raise HTTPException(status_code=500, detail=error_detail)
        
        if len(operation.response.generated_videos) == 0:
            logger.error("Generated videos list is empty")
            raise HTTPException(status_code=500, detail="비디오 확장 실패: 생성된 비디오가 없습니다.")
        
        # 비디오 다운로드
        generated_video = operation.response.generated_videos[0]
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        output_filename = f"output_{timestamp}.mp4"
        output_path = OUTPUTS_DIR / output_filename
        
        client.files.download(file=generated_video.video)
        generated_video.video.save(str(output_path))
        
        # 확장된 비디오 객체를 메모리에 저장 (반복 확장 가능)
        extended_video_uuid = str(uuid.uuid4())
        video_objects_cache[extended_video_uuid] = generated_video
        logger.info(f"Saved extended video object with UUID: {extended_video_uuid}")
        logger.info(f"Video extension completed: {output_filename}")
        
        # 이전 UUID는 캐시에서 제거 (메모리 관리)
        if video_uuid in video_objects_cache:
            del video_objects_cache[video_uuid]
            logger.info(f"Removed previous video object from cache: {video_uuid}")
        
        return JSONResponse({
            "status": "success",
            "message": "비디오가 확장되었습니다.",
            "output_file": f"/outputs/{output_filename}",
            "video_uuid": extended_video_uuid
        })
    
    except Exception as e:
        logger.error(f"Video extension error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/text-to-speech")
async def text_to_speech(
    prompt: str = Form(...),
    voice_name: str = Form("Zephyr")
):
    """Text to Speech 작업"""
    try:
        model = "gemini-2.5-pro-preview-tts"
        contents = [
            types.Content(
                role="user",
                parts=[types.Part.from_text(text=prompt)],
            ),
        ]
        generate_content_config = types.GenerateContentConfig(
            temperature=1,
            response_modalities=["audio"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=voice_name
                    )
                )
            ),
        )
        client = get_genai_client()
        
        for chunk in client.models.generate_content_stream(
            model=model,
            contents=contents,
            config=generate_content_config,
        ):
            if (
                chunk.candidates is None
                or chunk.candidates[0].content is None
                or chunk.candidates[0].content.parts is None
            ):
                continue
            
            if (chunk.candidates[0].content.parts[0].inline_data and 
                chunk.candidates[0].content.parts[0].inline_data.data):
                timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
                file_name = f"output_{timestamp}"
                inline_data = chunk.candidates[0].content.parts[0].inline_data
                data_buffer = inline_data.data
                file_extension = mimetypes.guess_extension(inline_data.mime_type)
                
                if file_extension is None:
                    file_extension = ".wav"
                    data_buffer = convert_to_wav(inline_data.data, inline_data.mime_type)
                
                output_filename = f"{file_name}{file_extension}"
                output_path = OUTPUTS_DIR / output_filename
                
                with open(output_path, "wb") as f:
                    f.write(data_buffer)
                
                return JSONResponse({
                    "status": "success",
                    "message": "음성이 생성되었습니다.",
                    "output_file": f"/outputs/{output_filename}"
                })
        
        raise HTTPException(status_code=500, detail="음성 생성 실패")
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# 프롬프트 관리 API
@app.get("/api/prompts")
async def get_prompts():
    """프롬프트 목록 조회"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT id, content, created_at FROM prompt ORDER BY created_at DESC")
    prompts = cursor.fetchall()
    conn.close()
    
    return JSONResponse({
        "prompts": [
            {
                "id": p[0],
                "content": p[1],
                "created_at": p[2]
            }
            for p in prompts
        ]
    })

@app.post("/api/prompts")
async def create_prompt(prompt_data: PromptCreate):
    """프롬프트 저장"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO prompt (content) VALUES (?)", (prompt_data.content,))
    conn.commit()
    prompt_id = cursor.lastrowid
    conn.close()
    
    return JSONResponse({
        "status": "success",
        "message": "프롬프트가 저장되었습니다.",
        "id": prompt_id
    })

@app.put("/api/prompts/{prompt_id}")
async def update_prompt(prompt_id: int, prompt_data: PromptCreate):
    """프롬프트 수정"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE prompt SET content = ?, created_at = CURRENT_TIMESTAMP WHERE id = ?",
        (prompt_data.content, prompt_id)
    )
    conn.commit()
    conn.close()
    
    return JSONResponse({
        "status": "success",
        "message": "프롬프트가 수정되었습니다."
    })

@app.delete("/api/prompts/{prompt_id}")
async def delete_prompt(prompt_id: int):
    """프롬프트 삭제"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM prompt WHERE id = ?", (prompt_id,))
    conn.commit()
    conn.close()
    
    return JSONResponse({
        "status": "success",
        "message": "프롬프트가 삭제되었습니다."
    })

if __name__ == "__main__":
    import uvicorn
    logger.info("Starting Gemini API Tools Web Application on port 33000")
    uvicorn.run(
        app, 
        host="0.0.0.0", 
        port=33000,
        log_level="info",
        access_log=True
    )

