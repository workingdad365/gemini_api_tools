import os
import re
import json
import time
import asyncio
import base64
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
from urllib.error import HTTPError, URLError
from urllib.request import Request as UrlRequest
from urllib.request import urlopen

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

# 앱 루트의 .env 파일 로드
app_env_path = BASE_DIR / ".env"
if app_env_path.exists():
    load_dotenv(app_env_path)
    logger.info(f"Loaded .env from {app_env_path}")
else:
    logger.warning(f".env file not found at {app_env_path}")

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


def compute_asset_version() -> str:
    """정적 자산(css/js)의 최종 수정 시각 기반 캐시 무효화용 버전 문자열을 생성한다.

    style.css와 main.js 중 가장 최근 수정 시각(epoch 초)을 버전으로 사용한다.
    파일이 실제로 변경되었을 때만 버전이 바뀌므로, 변경이 없으면 브라우저 캐시가
    그대로 재사용되어 불필요한 재다운로드가 발생하지 않는다.

    Returns:
        정수 형태의 버전 문자열. 대상 파일이 없으면 "1".
    """
    asset_files = [
        STATIC_DIR / "css" / "style.css",
        STATIC_DIR / "js" / "main.js",
    ]
    latest_mtime = 0.0
    for asset in asset_files:
        if asset.exists():
            latest_mtime = max(latest_mtime, asset.stat().st_mtime)
    return str(int(latest_mtime)) if latest_mtime else "1"


# 서버 시작 시점의 정적 자산 버전 (index.html 주입용)
ASSET_VERSION = compute_asset_version()
logger.info(f"Asset version for cache-busting: {ASSET_VERSION}")

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

# 환경변수에서 API 키 제거 (SDK 내부 경고 방지 - 명시적으로 키를 전달하므로 불필요)
os.environ.pop("GOOGLE_API_KEY", None)
os.environ.pop("GEMINI_API_KEY", None)

# 모델 설정 (고정 값)
STANDARD_MODEL = "gemini-3.1-flash-image"
LITE_MODEL = "gemini-3.1-flash-lite-image"
ADVANCED_MODEL = "gemini-3-pro-image"
STANDARD_MODEL_ALIAS = "Nano Banana 2"
LITE_MODEL_ALIAS = "Nano Banana 2 Lite"
ADVANCED_MODEL_ALIAS = "Nano Banana Pro"
PRO_MODEL = ADVANCED_MODEL
PRO_MODEL_ALIAS = ADVANCED_MODEL_ALIAS
logger.info(f"Model config - STANDARD: {STANDARD_MODEL} ({STANDARD_MODEL_ALIAS}), LITE: {LITE_MODEL} ({LITE_MODEL_ALIAS}), ADVANCED: {ADVANCED_MODEL} ({ADVANCED_MODEL_ALIAS})")

# Veo 3.1 모델 설정
VEO_STANDARD_MODEL = "veo-3.1-generate-preview"
VEO_FAST_MODEL = "veo-3.1-fast-generate-preview"
VEO_LITE_MODEL = "veo-3.1-lite-generate-preview"
VEO_MODELS = {
    VEO_STANDARD_MODEL: "Veo 3.1 Standard Preview",
    VEO_FAST_MODEL: "Veo 3.1 Fast Preview",
    VEO_LITE_MODEL: "Veo 3.1 Lite Preview",
}
VEO_DEFAULT_MODEL = VEO_LITE_MODEL
VEO_RESOLUTIONS = {
    VEO_STANDARD_MODEL: {"720p", "1080p", "4k"},
    VEO_FAST_MODEL: {"720p", "1080p", "4k"},
    VEO_LITE_MODEL: {"720p", "1080p"},
}

# ===== laozhang (OpenAI 호환 3rd-party 게이트웨이) 연동 설정 =====
# 이미지 생성/편집(Text to Image, Image to Image)에서 provider="laozhang"일 때만 사용된다.
# 비디오/TTS 및 기본 이미지 경로는 여전히 기존 Gemini SDK를 사용한다.
LAOZHANG_API_BASE_URL = os.getenv("LAOZHANG_API_BASE_URL", "https://api.laozhang.ai/v1")

_laozhang_key_list_str = os.getenv("LAOZHANG_API_KEY_LIST")
if _laozhang_key_list_str:
    laozhang_api_key_list = _laozhang_key_list_str.split()
else:
    _laozhang_single_key = os.getenv("LAOZHANG_API_KEY")
    laozhang_api_key_list = [_laozhang_single_key] if _laozhang_single_key else []

# laozhang 키가 하나라도 있으면 laozhang 모드 활성화 (프론트 체크박스 노출 근거)
LAOZHANG_AVAILABLE = bool(laozhang_api_key_list)
if LAOZHANG_AVAILABLE:
    logger.info(f"laozhang mode enabled - loaded {len(laozhang_api_key_list)} API key(s)")
else:
    logger.info("laozhang API key not configured; laozhang mode disabled")

# app.py 내부 모델명 -> laozhang(OpenAI 호환) 모델명 매핑.
# laozhang는 -preview 접미사를 사용하며 lite 전용 모델을 제공하지 않아 flash로 대체한다.
LAOZHANG_MODEL_MAP = {
    STANDARD_MODEL: "gemini-3.1-flash-image-preview",
    LITE_MODEL: "gemini-3.1-flash-image-preview",
    ADVANCED_MODEL: "gemini-3-pro-image-preview",
}

# laozhang 응답 본문에서 이미지를 추출하기 위한 정규식
_LAOZHANG_MD_IMG_RE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
_LAOZHANG_DATA_URL_RE = re.compile(r"data:(image/[^;]+);base64,([A-Za-z0-9+/=\s]+)")
_LAOZHANG_HTTP_URL_RE = re.compile(r"https?://[^\s)]+")


def _laozhang_headers() -> dict:
    """laozhang 호출용 헤더를 생성한다(랜덤 키 선택, 로그엔 마스킹)."""
    key = random.choice(laozhang_api_key_list)
    masked_key = key[:8] + "..." if len(key) > 8 else key
    logger.info(f"Selected laozhang API key: {masked_key} (from {len(laozhang_api_key_list)} keys)")
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}


def _laozhang_chat_completion(payload: dict, timeout: int = 180) -> dict:
    """laozhang의 OpenAI 호환 /chat/completions 엔드포인트를 호출하고 JSON을 반환한다.

    Args:
        payload: OpenAI chat/completions 요청 바디(model, messages 등).
        timeout: 요청 타임아웃(초).

    Returns:
        파싱된 응답 JSON dict.

    Raises:
        HTTPException: HTTP 오류(상태코드 전달) 또는 네트워크 오류(502).
    """
    url = LAOZHANG_API_BASE_URL.rstrip("/") + "/chat/completions"
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = UrlRequest(url, data=data, headers=_laozhang_headers(), method="POST")
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        logger.error(f"laozhang HTTP {exc.code}: {body}")
        status = exc.code if exc.code and exc.code >= 400 else 502
        raise HTTPException(status_code=status, detail=f"laozhang API 오류: {body[:500]}") from exc
    except URLError as exc:
        logger.error(f"laozhang connection error: {exc}")
        raise HTTPException(status_code=502, detail=f"laozhang 연결 실패: {exc}") from exc


def _laozhang_download(url: str, timeout: int = 120) -> tuple[bytes, str]:
    """laozhang가 URL로 돌려준 이미지를 다운로드하여 (바이트, MIME)를 반환한다."""
    request = UrlRequest(url, headers={"User-Agent": "gemini-api-tools/1.0"})
    try:
        with urlopen(request, timeout=timeout) as response:
            data = response.read()
            content_type = (
                response.headers.get("Content-Type")
                or mimetypes.guess_type(url)[0]
                or "image/png"
            )
            return data, content_type.split(";", 1)[0]
    except (HTTPError, URLError) as exc:
        logger.error(f"Failed to download laozhang asset: {exc}")
        raise HTTPException(status_code=502, detail=f"생성 결과 다운로드 실패: {exc}") from exc


def _parse_laozhang_image(content: str) -> tuple[Optional[bytes], str, str]:
    """laozhang chat 응답 본문에서 이미지(바이트/MIME)와 잔여 텍스트를 추출한다.

    laozhang는 이미지를 마크다운(![](data:image/...;base64,...)) 또는 URL 형태로 본문에
    임베드하여 반환한다. data URL이면 base64 디코드하고, http URL이면 다운로드한다.

    Args:
        content: chat 응답 message.content 문자열.

    Returns:
        (image_bytes, mime_type, text) 튜플. 이미지가 없으면 image_bytes는 None.
    """
    if not content:
        return None, "image/png", ""

    src: Optional[str] = None
    match = _LAOZHANG_MD_IMG_RE.search(content)
    if match:
        src = match.group(1).strip()
        text = _LAOZHANG_MD_IMG_RE.sub("", content).strip()
    else:
        data_match = _LAOZHANG_DATA_URL_RE.search(content)
        if data_match:
            src = data_match.group(0)
        else:
            url_match = _LAOZHANG_HTTP_URL_RE.search(content)
            src = url_match.group(0).rstrip(").,]") if url_match else None
        text = content.strip()

    image_bytes: Optional[bytes] = None
    mime_type = "image/png"
    if src and src.startswith("data:"):
        data_match = _LAOZHANG_DATA_URL_RE.search(src)
        if data_match:
            mime_type = data_match.group(1)
            image_bytes = base64.b64decode(re.sub(r"\s+", "", data_match.group(2)))
    elif src and src.startswith("http"):
        image_bytes, mime_type = _laozhang_download(src)

    return image_bytes, mime_type, text


def laozhang_generate_image(
    prompt: str,
    model: str,
    input_images: Optional[list[tuple[bytes, str]]] = None,
    aspect_ratio: Optional[str] = None,
    resolution: Optional[str] = None,
) -> tuple[Optional[bytes], str, str]:
    """laozhang(OpenAI 호환) chat/completions로 이미지를 생성 또는 편집한다.

    OpenAI chat/completions에는 비율/해상도 전용 필드가 없으므로 프롬프트에 자연어 지시로
    부가한다. 입력 이미지는 data URL(image_url)로 전달한다.

    Args:
        prompt: 사용자 프롬프트.
        model: app.py 내부 모델명(내부에서 laozhang 모델명으로 매핑).
        input_images: 편집/참조용 입력 이미지 (바이트, MIME) 리스트.
        aspect_ratio: 이미지 비율(예: "16:9"). 신규 text-to-image에서만 의미.
        resolution: 해상도(예: "1K", "2K"). 프롬프트 지시로 부가.

    Returns:
        (image_bytes, mime_type, text) 튜플. 이미지가 없으면 image_bytes는 None.

    Raises:
        HTTPException: laozhang API/네트워크 오류 또는 응답 형식 이상.
    """
    laozhang_model = LAOZHANG_MODEL_MAP.get(model, model)

    directives: list[str] = []
    if aspect_ratio:
        directives.append(f"aspect ratio {aspect_ratio}")
    if resolution:
        directives.append(f"{resolution} resolution")
    full_prompt = prompt if not directives else f"{prompt}\n\n(Output image: {', '.join(directives)}.)"

    message_content: list[dict] = [{"type": "text", "text": full_prompt}]
    for img_bytes, img_mime in input_images or []:
        encoded = base64.b64encode(img_bytes).decode("utf-8")
        message_content.append({
            "type": "image_url",
            "image_url": {"url": f"data:{img_mime};base64,{encoded}"},
        })

    payload = {"model": laozhang_model, "messages": [{"role": "user", "content": message_content}]}
    logger.info(
        f"Calling laozhang chat/completions - model: {laozhang_model}, input_images: {len(input_images or [])}"
    )
    response = _laozhang_chat_completion(payload)

    choices = response.get("choices") or []
    if not choices:
        raise HTTPException(status_code=502, detail=f"laozhang 응답에 choices가 없습니다: {response}")

    raw_content = (choices[0].get("message") or {}).get("content")
    if isinstance(raw_content, list):
        # content가 파트 리스트로 오는 경우 텍스트 파트만 결합
        text_content = "".join(
            part.get("text", "") for part in raw_content if isinstance(part, dict)
        )
    else:
        text_content = raw_content or ""

    return _parse_laozhang_image(text_content)


def get_genai_client() -> genai.Client:
    """매 요청마다 랜덤 API 키를 선택하여 새 클라이언트 생성"""
    selected_key = random.choice(api_key_list)
    # 키의 앞 8자만 표시 (보안)
    masked_key = selected_key[:8] + "..." if len(selected_key) > 8 else selected_key
    logger.info(f"Selected API key: {masked_key} (from {len(api_key_list)} keys)")
    return genai.Client(api_key=selected_key)


def validate_veo_options(model: str, resolution: str) -> None:
    """Veo 모델과 해상도 조합이 공식 지원 범위인지 검증한다.

    Args:
        model: Gemini API에 전달할 Veo 모델 코드.
        resolution: 요청한 출력 해상도.

    Raises:
        HTTPException: 알 수 없는 모델이거나 해당 모델이 지원하지 않는 해상도인 경우.
    """
    if model not in VEO_MODELS:
        raise HTTPException(status_code=400, detail="지원하지 않는 Veo 모델입니다.")
    if resolution not in VEO_RESOLUTIONS[model]:
        model_alias = VEO_MODELS[model]
        supported = ", ".join(sorted(VEO_RESOLUTIONS[model]))
        raise HTTPException(
            status_code=400,
            detail=f"{model_alias}은(는) {resolution} 해상도를 지원하지 않습니다. 지원 해상도: {supported}",
        )

# 비디오 객체 저장소 (메모리)
# UUID -> {"video": generated_video 객체, "model": Veo 모델 코드} 매핑
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

# Favicon 라우트 (브라우저 기본 요청 처리)
@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return FileResponse(STATIC_DIR / "img" / "favicon.ico")

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
def pil_to_bytes(pil_image, image_format: str = 'JPEG') -> bytes:
    """PIL Image 또는 google-genai Image를 bytes로 변환"""
    # google-genai Image 객체는 image_bytes 속성으로 원본 바이트를 보유
    image_bytes = getattr(pil_image, "image_bytes", None)
    if image_bytes is not None:
        return image_bytes
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

async def read_upload_images(files: list[UploadFile]) -> list[tuple[bytes, str]]:
    """업로드된 이미지 파일들을 (바이트, MIME) 튜플 리스트로 읽어 반환한다.

    디스크에 저장하지 않고 메모리로만 읽어 Interactions API 입력으로 사용한다.

    Args:
        files: FastAPI UploadFile 리스트.

    Returns:
        (원본 바이트, MIME 타입) 튜플의 리스트.
    """
    result: list[tuple[bytes, str]] = []
    for file in files:
        data = await file.read()
        mime_type = file.content_type or mimetypes.guess_type(file.filename or "")[0] or "image/png"
        result.append((data, mime_type))
    return result

def generate_image_via_interaction(
    client: genai.Client,
    model: str,
    prompt: str,
    previous_interaction_id: Optional[str] = None,
    input_images: Optional[list[tuple[bytes, str]]] = None,
    aspect_ratio: Optional[str] = None,
    resolution: Optional[str] = None,
) -> tuple[Optional[str], Optional[bytes], str, str]:
    """Interactions API로 이미지를 생성 또는 편집하고 결과를 반환한다.

    기존 generateContent + 수동 대화기록 관리 대신, 서버 사이드 상태(previous_interaction_id)를
    사용하여 멀티턴을 처리한다. 이전 이미지 바이트를 직접 재전송할 필요가 없다.

    Args:
        client: GenAI 클라이언트.
        model: 이미지 모델 이름.
        prompt: 사용자 프롬프트.
        previous_interaction_id: 이어갈 이전 interaction ID. 없으면 새 대화 시작.
        input_images: 입력 이미지 (바이트, MIME) 리스트. image-to-image 신규 생성 시 사용.
        aspect_ratio: 이미지 비율(예: "16:9"). text-to-image에서만 사용.
        resolution: 해상도. "0.5K"는 API 규격상 "512"로 매핑되며, 그 외("1K"/"2K"/"4K")는 그대로 전달.

    Returns:
        (interaction_id, image_bytes, mime_type, text) 튜플.
        image_bytes는 이미지가 없으면 None(텍스트 전용 응답).
    """
    # 입력 구성: 이미지(선택) + 텍스트
    input_items: list[dict] = []
    if input_images:
        for img_bytes, img_mime in input_images:
            input_items.append({
                "type": "image",
                "data": base64.b64encode(img_bytes).decode("utf-8"),
                "mime_type": img_mime,
            })
    input_items.append({"type": "text", "text": prompt})

    # 이미지 config (해상도/비율) - 신규 생성 턴에서만 사용
    image_config: dict = {}
    if aspect_ratio:
        image_config["aspect_ratio"] = aspect_ratio
    if resolution:
        image_config["image_size"] = "512" if resolution == "0.5K" else resolution

    body: dict = {
        "model": model,
        "input": input_items,
        "response_modalities": ["text", "image"],
        # 서버 사이드 상태 지속 저장 (멀티턴 편집 시 previous_interaction_id 참조 보장).
        "store": True,
    }
    if previous_interaction_id:
        # 편집(이어가기) 호출에는 generation_config/image_config를 재전송하지 않는다.
        # 재전송하면 지연 후 서버가 404("Requested entity was not found")를 반환한다(검증 완료).
        # 비율/해상도는 원본 interaction의 컨텍스트를 그대로 상속한다.
        body["previous_interaction_id"] = previous_interaction_id
    elif image_config:
        body["generation_config"] = {"image_config": image_config}

    # 편집(이어가기) 턴은 preview 단계 Interactions API에서 간헐적으로
    # thought_signature(400) 또는 not_found(404) 오류가 발생할 수 있어 짧은 백오프로 재시도한다.
    # (문서상 stateful 모드에선 서버가 signature를 관리하나 preview라 이따금 실패함)
    is_continuation = previous_interaction_id is not None
    max_attempts = 3 if is_continuation else 1
    interaction = None
    for attempt in range(max_attempts):
        try:
            interaction = client.interactions.create(**body)
            break
        except Exception as exc:
            message = str(exc)
            transient = ("thought_signature" in message) or ("not found" in message.lower())
            if is_continuation and transient and attempt < max_attempts - 1:
                logger.warning(
                    f"Transient interaction error on edit turn "
                    f"(attempt {attempt + 1}/{max_attempts}), retrying: {message[:120]}"
                )
                time.sleep(1.5 * (attempt + 1))
                continue
            raise

    # 출력 파싱
    text = interaction.output_text or ""
    image_bytes: Optional[bytes] = None
    mime_type = "image/png"
    out_img = getattr(interaction, "output_image", None)
    if out_img and out_img.data:
        data = out_img.data
        image_bytes = data if isinstance(data, bytes) else base64.b64decode(data)
        mime_type = out_img.mime_type or "image/jpeg"

    return interaction.id, image_bytes, mime_type, text

# 썸네일 설정
THUMBNAIL_SUFFIX = ".thumb"  # 썸네일 파일 접미사 (예: output_xxx.png -> output_xxx.png.thumb)
THUMBNAIL_MAX_SIZE = 320  # 썸네일 최대 변(px)


def create_thumbnail(original_path: Path, image_bytes: bytes) -> Optional[Path]:
    """원본 이미지에 대한 축소 썸네일(PNG)을 생성하여 저장한다.

    원본 파일명이 aaa.png이면 썸네일 파일명은 aaa.png.thumb가 되며, 내용은 PNG 포맷이다.
    썸네일 생성이 실패해도 원본 저장 흐름에는 영향을 주지 않도록 예외를 흡수한다.

    Args:
        original_path: 원본 이미지 파일 경로.
        image_bytes: 원본 이미지 바이트.

    Returns:
        생성된 썸네일 파일 경로. 실패 시 None.
    """
    thumb_path = original_path.with_name(original_path.name + THUMBNAIL_SUFFIX)
    try:
        with Image.open(BytesIO(image_bytes)) as img:
            # 팔레트/투명 이미지는 RGBA로, 그 외는 RGB로 정규화
            img = img.convert("RGBA") if img.mode in ("RGBA", "LA", "P") else img.convert("RGB")
            img.thumbnail((THUMBNAIL_MAX_SIZE, THUMBNAIL_MAX_SIZE))
            img.save(thumb_path, format="PNG")
        logger.info(f"Thumbnail created: {thumb_path.name}")
        return thumb_path
    except (OSError, ValueError) as exc:
        logger.warning(f"Thumbnail creation failed for {original_path.name}: {exc}")
        return None


def save_output_image(image_bytes: bytes, mime_type: str) -> str:
    """생성된 이미지 바이트를 outputs 디렉토리에 저장하고 공개 URL 경로를 반환한다.

    원본 저장 후 사이드 갤러리 표시용 썸네일(PNG)을 자동으로 함께 생성한다.

    Args:
        image_bytes: 저장할 이미지 원본 바이트.
        mime_type: 이미지 MIME 타입(확장자 결정에 사용).

    Returns:
        "/outputs/<파일명>" 형태의 URL 경로.
    """
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    extension = mimetypes.guess_extension(mime_type) or ".png"
    output_filename = f"output_{timestamp}{extension}"
    output_path = OUTPUTS_DIR / output_filename
    output_path.write_bytes(image_bytes)
    logger.info(f"Image saved successfully: {output_filename}")
    # 새로 생성되는 이미지에 대해서만 썸네일 생성 (기존 이미지는 백필하지 않음)
    create_thumbnail(output_path, image_bytes)
    return f"/outputs/{output_filename}"

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

@app.api_route("/", methods=["GET", "HEAD"]) # HEAD 메서드 명시적 추가
async def read_root(request: Request, session_token: str = Cookie(None)):
    """메인 페이지 (인증 필요)"""
    
    # 1. UptimeRobot 등 모니터링 봇을 위한 예외 처리 (선택 사항)
    # 봇은 쿠키가 없으므로 항상 302 리다이렉트가 발생.
    if request.method == "HEAD":
        return Response(status_code=200) # HEAD 요청에는 즉시 200 응답

    if not verify_session(session_token):
        return RedirectResponse(url="/login", status_code=302)

    # index.html에 정적 자산 버전을 주입하여 캐시 무효화 처리
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    html = html.replace("{{ASSET_VERSION}}", ASSET_VERSION)
    return HTMLResponse(content=html)


# @app.get("/")
# async def read_root(request: Request, session_token: str = Cookie(None)):
#     """메인 페이지 (인증 필요)"""
#     if not verify_session(session_token):
#         return RedirectResponse(url="/login", status_code=302)
#     return FileResponse(str(STATIC_DIR / "index.html"))

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

@app.get("/api/config")
async def get_config():
    """프론트엔드에 모델 설정 정보 제공"""
    return JSONResponse({
        "standard_model": STANDARD_MODEL,
        "lite_model": LITE_MODEL,
        "pro_model": PRO_MODEL,
        "advanced_model": ADVANCED_MODEL,
        "standard_model_alias": STANDARD_MODEL_ALIAS,
        "lite_model_alias": LITE_MODEL_ALIAS,
        "pro_model_alias": PRO_MODEL_ALIAS,
        "advanced_model_alias": ADVANCED_MODEL_ALIAS,
        "video_standard_model": VEO_STANDARD_MODEL,
        "video_fast_model": VEO_FAST_MODEL,
        "video_lite_model": VEO_LITE_MODEL,
        "video_default_model": VEO_DEFAULT_MODEL,
        "video_model_aliases": VEO_MODELS,
        # laozhang(3rd-party) 모드 사용 가능 여부. 프론트 체크박스 노출 조건으로 사용된다.
        "laozhang_available": LAOZHANG_AVAILABLE,
    })

# 출력 이미지 갤러리 API
@app.get("/api/gallery")
async def get_gallery():
    """썸네일이 존재하는 출력 이미지 목록을 최신순으로 반환한다.

    썸네일(.thumb)이 있는 파일만 대상으로 하므로, 썸네일 도입 이전에 생성된
    과거 이미지는 목록에 포함되지 않는다.

    Returns:
        {"images": [{filename, thumb_url, original_url, mtime}, ...]} 형태의 JSON.
        mtime 내림차순(최신이 먼저)으로 정렬된다.
    """
    images = []
    for thumb_path in OUTPUTS_DIR.glob(f"*{THUMBNAIL_SUFFIX}"):
        # 썸네일명 output_xxx.png.thumb -> 원본명 output_xxx.png
        original_path = thumb_path.with_suffix("")
        if not original_path.exists():
            continue
        images.append({
            "filename": original_path.name,
            "thumb_url": f"/api/thumbnail/{thumb_path.name}",
            "original_url": f"/outputs/{original_path.name}",
            "mtime": original_path.stat().st_mtime,
        })
    images.sort(key=lambda item: item["mtime"], reverse=True)
    return JSONResponse({"images": images})


def _resolve_output_path(filename: str) -> Path:
    """outputs 디렉토리 내부 파일 경로를 안전하게 해석한다(경로 순회 방지).

    Args:
        filename: 파일명(디렉토리 구분자 미포함 가정).

    Returns:
        검증된 절대 경로.

    Raises:
        HTTPException: 경로가 outputs 디렉토리를 벗어나는 경우(400).
    """
    outputs_root = OUTPUTS_DIR.resolve()
    target = (OUTPUTS_DIR / filename).resolve()
    if outputs_root not in target.parents and target != outputs_root:
        logger.warning(f"Path traversal attempt blocked: {filename}")
        raise HTTPException(status_code=400, detail="잘못된 파일 경로입니다.")
    return target


@app.get("/api/thumbnail/{filename}")
async def get_thumbnail(filename: str):
    """썸네일 파일(.thumb, PNG 내용)을 image/png 타입으로 반환한다."""
    thumb_path = _resolve_output_path(filename)
    if not thumb_path.exists() or not thumb_path.name.endswith(THUMBNAIL_SUFFIX):
        raise HTTPException(status_code=404, detail="썸네일을 찾을 수 없습니다.")
    return FileResponse(thumb_path, media_type="image/png")


@app.delete("/api/outputs/{filename}")
async def delete_output(filename: str):
    """출력 이미지 원본과 해당 썸네일을 함께 삭제한다."""
    original_path = _resolve_output_path(filename)
    thumb_path = original_path.with_name(original_path.name + THUMBNAIL_SUFFIX)

    deleted_any = False
    if original_path.exists():
        original_path.unlink()
        deleted_any = True
        logger.info(f"Deleted output image: {original_path.name}")
    if thumb_path.exists():
        thumb_path.unlink()
        deleted_any = True
        logger.info(f"Deleted thumbnail: {thumb_path.name}")

    if not deleted_any:
        raise HTTPException(status_code=404, detail="이미지를 찾을 수 없습니다.")

    return JSONResponse({"status": "success", "message": "이미지가 삭제되었습니다."})


def _build_image_response(image_bytes: Optional[bytes], mime_type: str, text_response: str, current_session_id: str) -> JSONResponse:
    """이미지/텍스트 응답을 Gemini 경로와 동일한 JSON 스펙으로 구성한다."""
    if image_bytes:
        output_file = save_output_image(image_bytes, mime_type)
        response_data = {
            "status": "success",
            "message": "이미지가 생성되었습니다.",
            "output_file": output_file,
            "session_id": current_session_id,
        }
        if text_response:
            response_data["llm_response"] = text_response
        return JSONResponse(response_data)

    if text_response:
        logger.info("laozhang: no image generated, but text response received")
        return JSONResponse({
            "status": "success",
            "message": "텍스트 응답을 받았습니다.",
            "text_only": True,
            "llm_response": text_response,
            "session_id": current_session_id,
        })

    logger.error("laozhang: no image or text data received")
    raise HTTPException(status_code=500, detail="응답 데이터 없음")


async def _text_to_image_laozhang(
    prompt: str,
    aspect_ratio: str,
    model: str,
    resolution: str,
    is_new: bool,
    session_id: Optional[str],
) -> JSONResponse:
    """laozhang(3rd-party) 경로의 Text to Image 처리.

    laozhang는 서버 사이드 상태(previous_interaction_id)가 없어, 멀티턴 편집 시에는
    세션에 저장해 둔 직전 결과 이미지를 재전송하여 이어간다.
    """
    if not LAOZHANG_AVAILABLE:
        raise HTTPException(status_code=400, detail="laozhang API 키가 설정되지 않았습니다. (.env의 LAOZHANG_API_KEY)")

    continue_session = not is_new and session_id and session_id in image_chat_sessions
    input_images: Optional[list[tuple[bytes, str]]] = None
    if continue_session:
        previous = image_chat_sessions[session_id]
        previous_bytes = previous.get("image_bytes")
        if not previous_bytes:
            raise HTTPException(status_code=400, detail="편집할 이전 laozhang 세션 정보가 없습니다.")
        input_images = [(previous_bytes, previous.get("mime_type", "image/png"))]
        logger.info(f"Continuing laozhang session (image resend): {session_id}")

    image_bytes, mime_type, text_response = laozhang_generate_image(
        prompt,
        model,
        input_images=input_images,
        # 이어가기 턴에는 비율을 재지정하지 않고 원본 맥락을 유지
        aspect_ratio=aspect_ratio if not continue_session else None,
        resolution=resolution,
    )

    current_session_id = session_id if continue_session else str(uuid.uuid4())
    if not continue_session:
        logger.info(f"Created new laozhang chat session: {current_session_id}")

    # 다음 턴을 위해 최신 결과 이미지를 세션에 저장 (laozhang은 서버 상태가 없으므로 필수)
    if image_bytes:
        image_chat_sessions[current_session_id] = {
            "provider": "laozhang",
            "image_bytes": image_bytes,
            "mime_type": mime_type,
            "model": model,
        }

    return _build_image_response(image_bytes, mime_type, text_response, current_session_id)


async def _image_to_image_laozhang(
    prompt: str,
    files: Optional[list[UploadFile]],
    model: str,
    resolution: str,
    is_new: bool,
    session_id: Optional[str],
) -> JSONResponse:
    """laozhang(3rd-party) 경로의 Image to Image 처리."""
    if not LAOZHANG_AVAILABLE:
        raise HTTPException(status_code=400, detail="laozhang API 키가 설정되지 않았습니다. (.env의 LAOZHANG_API_KEY)")

    continue_session = not is_new and session_id and session_id in image_chat_sessions
    if continue_session:
        previous = image_chat_sessions[session_id]
        previous_bytes = previous.get("image_bytes")
        if not previous_bytes:
            raise HTTPException(status_code=400, detail="편집할 이전 laozhang 세션 정보가 없습니다.")
        input_images: list[tuple[bytes, str]] = [(previous_bytes, previous.get("mime_type", "image/png"))]
        # 편집 중 추가 참조 이미지를 올린 경우 함께 전달 (직전 결과 1장 + 추가 13장)
        if files:
            input_images.extend(await read_upload_images(files[:13]))
        current_session_id = session_id
        logger.info(f"Continuing laozhang image session (image resend): {session_id}")
    else:
        if not files:
            raise HTTPException(status_code=400, detail="새로 만들기 모드에서는 이미지 파일이 필요합니다.")
        input_images = await read_upload_images(files[:14])
        current_session_id = str(uuid.uuid4())
        logger.info(f"Created new laozhang chat session for image-to-image: {current_session_id}")

    image_bytes, mime_type, text_response = laozhang_generate_image(
        prompt,
        model,
        input_images=input_images,
        resolution=resolution,
    )

    if image_bytes:
        image_chat_sessions[current_session_id] = {
            "provider": "laozhang",
            "image_bytes": image_bytes,
            "mime_type": mime_type,
            "model": model,
        }

    return _build_image_response(image_bytes, mime_type, text_response, current_session_id)


@app.post("/api/text-to-image")
async def text_to_image(
    prompt: str = Form(...),
    aspect_ratio: str = Form("16:9"),
    model: str = Form(None),
    resolution: str = Form("1K"),
    is_new: bool = Form(True),
    session_id: Optional[str] = Form(None),
    provider: str = Form("gemini")
):
    """Text to Image 작업 (Interactions API 기반 Multi-turn 지원).

    provider="laozhang"이면 OpenAI 호환 3rd-party 게이트웨이로 생성/편집하고,
    그 외("gemini", 기본값)이면 기존 Gemini Interactions API 경로를 사용한다.
    """
    try:
        if model is None:
            model = STANDARD_MODEL
        logger.info(f"Text to Image request - provider: {provider}, prompt length: {len(prompt)}, aspect_ratio: {aspect_ratio}, model: {model}, resolution: {resolution}, is_new: {is_new}, session_id: {session_id}")
        logger.info(f"Text to Image prompt: {prompt}")

        # ===== laozhang(3rd-party) 경로 =====
        if provider == "laozhang":
            return await _text_to_image_laozhang(prompt, aspect_ratio, model, resolution, is_new, session_id)

        client = get_genai_client()

        # Multi-turn 모드: 이전 interaction ID를 이어받아 서버 사이드 상태로 편집
        previous_interaction_id: Optional[str] = None
        continue_session = not is_new and session_id and session_id in image_chat_sessions
        if continue_session:
            previous_interaction_id = image_chat_sessions[session_id].get("interaction_id")
            if not previous_interaction_id:
                raise HTTPException(status_code=400, detail="편집할 이전 세션 정보가 없습니다.")
            logger.info(f"Continuing interaction: {previous_interaction_id} (session: {session_id})")

        logger.info("Calling Interactions API...")
        interaction_id, image_bytes, mime_type, text_response = generate_image_via_interaction(
            client,
            model,
            prompt,
            previous_interaction_id=previous_interaction_id,
            aspect_ratio=aspect_ratio,
            resolution=resolution,
        )

        # 세션 ID 결정 (이어가기면 기존 유지, 아니면 신규 발급)
        current_session_id = session_id if continue_session else str(uuid.uuid4())
        if not continue_session:
            logger.info(f"Created new chat session: {current_session_id}")

        # 다음 턴을 위해 최신 interaction ID 저장
        image_chat_sessions[current_session_id] = {
            "interaction_id": interaction_id,
            "model": model,
        }

        if image_bytes:
            output_file = save_output_image(image_bytes, mime_type)
            response_data = {
                "status": "success",
                "message": "이미지가 생성되었습니다.",
                "output_file": output_file,
                "session_id": current_session_id,
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
                "session_id": current_session_id,
            })

        logger.error("No image or text data received from API")
        raise HTTPException(status_code=500, detail="응답 데이터 없음")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Text to Image error: {str(e)}")
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/image-to-image")
async def image_to_image(
    prompt: str = Form(...),
    files: list[UploadFile] = File(None),
    model: str = Form(None),
    resolution: str = Form("1K"),
    is_new: bool = Form(True),
    session_id: Optional[str] = Form(None),
    provider: str = Form("gemini")
):
    """Image to Image 작업 (Interactions API 기반, 멀티 이미지 및 Multi-turn 지원).

    provider="laozhang"이면 OpenAI 호환 3rd-party 게이트웨이를 사용한다.
    """
    try:
        if model is None:
            model = STANDARD_MODEL
        logger.info(f"Image to Image request - provider: {provider}, model: {model}, resolution: {resolution}, is_new: {is_new}, session_id: {session_id}")
        logger.info(f"Image to Image prompt: {prompt}")

        # ===== laozhang(3rd-party) 경로 =====
        if provider == "laozhang":
            return await _image_to_image_laozhang(prompt, files, model, resolution, is_new, session_id)

        client = get_genai_client()

        previous_interaction_id: Optional[str] = None
        input_images: Optional[list[tuple[bytes, str]]] = None
        continue_session = not is_new and session_id and session_id in image_chat_sessions

        if continue_session:
            # Multi-turn 편집: 서버 사이드 상태를 이어받음 (이전 이미지 재전송 불필요)
            previous_interaction_id = image_chat_sessions[session_id].get("interaction_id")
            if not previous_interaction_id:
                raise HTTPException(status_code=400, detail="편집할 이전 세션 정보가 없습니다.")
            # 편집 중 추가 참조 이미지를 올린 경우 함께 전달
            if files:
                input_images = await read_upload_images(files[:14])
            logger.info(f"Continuing image interaction: {previous_interaction_id} (session: {session_id})")
        else:
            # 새 세션: 업로드 이미지 + 프롬프트로 생성
            if not files:
                raise HTTPException(status_code=400, detail="새로 만들기 모드에서는 이미지 파일이 필요합니다.")
            # 두 모델 모두 최대 14장 이미지 참조 지원
            files_to_process = files[:14]
            logger.info(f"Processing {len(files_to_process)} images for image-to-image with model {model}")
            input_images = await read_upload_images(files_to_process)

        logger.info("Calling Interactions API...")
        interaction_id, image_bytes, mime_type, text_response = generate_image_via_interaction(
            client,
            model,
            prompt,
            previous_interaction_id=previous_interaction_id,
            input_images=input_images,
            resolution=resolution,
        )

        current_session_id = session_id if continue_session else str(uuid.uuid4())
        if not continue_session:
            logger.info(f"Created new chat session for image-to-image: {current_session_id}")

        # 다음 턴을 위해 최신 interaction ID 저장
        image_chat_sessions[current_session_id] = {
            "interaction_id": interaction_id,
            "model": model,
        }

        if image_bytes:
            output_file = save_output_image(image_bytes, mime_type)
            response_data = {
                "status": "success",
                "message": "이미지가 생성되었습니다.",
                "output_file": output_file,
                "session_id": current_session_id,
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
                "session_id": current_session_id,
            })

        logger.error("No image data or text response received from API")
        raise HTTPException(status_code=500, detail="이미지 생성 실패: 응답에 이미지 데이터가 없습니다.")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Image to Image error: {str(e)}")
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/text-to-video")
async def text_to_video(
    prompt: str = Form(...),
    model: str = Form(VEO_DEFAULT_MODEL),
    resolution: str = Form("720p"),
    aspect_ratio: str = Form("16:9")
):
    """Text to Video 작업"""
    try:
        validate_veo_options(model, resolution)
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
            await asyncio.sleep(10)
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
        video_objects_cache[video_uuid] = {"video": generated_video, "model": model}
        logger.info(f"Saved video object with UUID: {video_uuid}, model: {model}")
        
        return JSONResponse({
            "status": "success",
            "message": "비디오가 생성되었습니다.",
            "output_file": f"/outputs/{output_filename}",
            "video_uuid": video_uuid,
            "model": model,
        })
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/image-to-video")
async def image_to_video(
    prompt: str = Form(...),
    files: list[UploadFile] = File(...),
    model: str = Form(VEO_DEFAULT_MODEL),
    resolution: str = Form("720p"),
    aspect_ratio: str = Form("16:9")
):
    """Image to Video 작업 (멀티 이미지 지원)"""
    upload_paths = []
    try:
        validate_veo_options(model, resolution)

        # 최대 3개까지만 처리
        files_to_process = files[:3]
        if model == VEO_LITE_MODEL and len(files_to_process) > 1:
            raise HTTPException(
                status_code=400,
                detail="Veo 3.1 Lite는 시작 이미지 1장만 지원합니다. 여러 참조 이미지는 Standard 또는 Fast를 선택하세요.",
            )
        logger.info(f"Processing {len(files_to_process)} images for image-to-video")
        
        # 파일 저장
        for file in files_to_process:
            upload_path = UPLOADS_DIR / file.filename
            with open(upload_path, "wb") as buffer:
                buffer.write(await file.read())
            upload_paths.append(upload_path)
        
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
            await asyncio.sleep(10)
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
        video_objects_cache[video_uuid] = {"video": video, "model": model}
        logger.info(f"Saved video object with UUID: {video_uuid}, model: {model}")
        
        # 업로드된 파일 삭제
        for upload_path in upload_paths:
            if upload_path.exists():
                upload_path.unlink()
        
        return JSONResponse({
            "status": "success",
            "message": "비디오가 생성되었습니다.",
            "output_file": f"/outputs/{output_filename}",
            "video_uuid": video_uuid,
            "model": model,
        })
    except HTTPException:
        raise
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
        
        cached_video = video_objects_cache[video_uuid]
        if isinstance(cached_video, dict):
            previous_video = cached_video["video"]
            model = cached_video["model"]
        else:
            # 서버 재시작 전 형식과의 호환성 유지
            previous_video = cached_video
            model = VEO_STANDARD_MODEL
        logger.info(f"Retrieved video object from cache: {video_uuid}")

        if model == VEO_LITE_MODEL:
            raise HTTPException(status_code=400, detail="Veo 3.1 Lite로 생성한 비디오는 확장할 수 없습니다.")
        if resolution != "720p":
            raise HTTPException(status_code=400, detail="비디오 확장은 720p 해상도만 지원합니다.")

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
            await asyncio.sleep(10)
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
        video_objects_cache[extended_video_uuid] = {"video": generated_video, "model": model}
        logger.info(f"Saved extended video object with UUID: {extended_video_uuid}, model: {model}")
        logger.info(f"Video extension completed: {output_filename}")
        
        # 이전 UUID는 캐시에서 제거 (메모리 관리)
        if video_uuid in video_objects_cache:
            del video_objects_cache[video_uuid]
            logger.info(f"Removed previous video object from cache: {video_uuid}")
        
        return JSONResponse({
            "status": "success",
            "message": "비디오가 확장되었습니다.",
            "output_file": f"/outputs/{output_filename}",
            "video_uuid": extended_video_uuid,
            "model": model,
        })
    except HTTPException:
        raise
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
        access_log=True,
        timeout_graceful_shutdown=10,
    )

