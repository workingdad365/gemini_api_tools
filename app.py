import os
import json
import time
import asyncio
import base64
import sqlite3
import mimetypes
import struct
import shutil
import subprocess
import logging
import traceback
import uuid
from datetime import datetime
from io import BytesIO
from typing import Optional
from pathlib import Path

import secrets
import hashlib
from collections import defaultdict

from fastapi import FastAPI, File, UploadFile, Form, HTTPException, Request, Response, Cookie, Depends, Query
from fastapi.responses import FileResponse, JSONResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from starlette.datastructures import Headers

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

# 앱 루트의 .env 파일만 로드한다. 서비스 프로세스에 남아 있는 기존 환경 변수보다
# 현재 .env 값을 우선하여 배포 후 키 변경 사항이 확실히 반영되도록 한다.
app_env_path = BASE_DIR / ".env"
if app_env_path.exists():
    load_dotenv(app_env_path, override=True)
    logger.info(f"Loaded .env from {app_env_path}")
else:
    logger.error(f".env file not found at {app_env_path}")
    raise FileNotFoundError(f".env file not found at {app_env_path}")

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

# Gemini API 키 초기화
gemini_api_key = os.getenv("GEMINI_API_KEY", "").strip()
if not gemini_api_key:
    logger.error("GEMINI_API_KEY not found in environment variables")
    raise ValueError("GEMINI_API_KEY not found in environment variables")
if any(character.isspace() for character in gemini_api_key):
    logger.error("GEMINI_API_KEY contains whitespace")
    raise ValueError("GEMINI_API_KEY must contain exactly one API key without whitespace")
logger.info("GEMINI_API_KEY loaded successfully")

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
OMNI_MODEL = "gemini-omni-1.1-flash"
VEO_MODELS = {
    VEO_STANDARD_MODEL: "Veo 3.1 Standard Preview",
    VEO_FAST_MODEL: "Veo 3.1 Fast Preview",
    VEO_LITE_MODEL: "Veo 3.1 Lite Preview",
    OMNI_MODEL: "Gemini Omni 1.1 Flash",
}
VEO_DEFAULT_MODEL = VEO_LITE_MODEL
VEO_RESOLUTIONS = {
    VEO_STANDARD_MODEL: {"720p", "1080p", "4k"},
    VEO_FAST_MODEL: {"720p", "1080p", "4k"},
    VEO_LITE_MODEL: {"720p", "1080p"},
    OMNI_MODEL: {"360p", "720p", "1080p", "4k"},
}

# Gemini Developer API 유료 등급 표준 가격 (2026-08-28 기준)
IMAGE_OUTPUT_PRICES = {
    STANDARD_MODEL: {"0.5K": 0.045, "1K": 0.067, "2K": 0.101, "4K": 0.151},
    LITE_MODEL: {"1K": 0.0336},
    ADVANCED_MODEL: {"1K": 0.134, "2K": 0.134, "4K": 0.24},
}
VIDEO_PRICES_PER_SECOND = {
    VEO_STANDARD_MODEL: {"720p": 0.40, "1080p": 0.40, "4k": 0.60},
    VEO_FAST_MODEL: {"720p": 0.10, "1080p": 0.12, "4k": 0.30},
    VEO_LITE_MODEL: {"720p": 0.05, "1080p": 0.08},
    OMNI_MODEL: {"720p": 0.10},
}
OMNI_INPUT_PRICE_PER_MILLION_TOKENS = 1.50
OMNI_TEXT_OUTPUT_PRICE_PER_MILLION_TOKENS = 9.00
OMNI_VIDEO_OUTPUT_PRICE_PER_MILLION_TOKENS = 17.50
OMNI_MAX_VIDEO_EXTENSIONS = 3
OMNI_720P_TOKENS_PER_SECOND = 5_792
VIDEO_DURATIONS_SECONDS = {
    VEO_STANDARD_MODEL: 8,
    VEO_FAST_MODEL: 8,
    VEO_LITE_MODEL: 8,
    OMNI_MODEL: 10,
}
TTS_MODEL = "gemini-2.5-pro-preview-tts"
TTS_INPUT_PRICE_PER_MILLION_TOKENS = 1.00
TTS_OUTPUT_PRICE_PER_MILLION_TOKENS = 20.00
TTS_AUDIO_TOKENS_PER_SECOND = 25
PRICING_UPDATED_AT = "2026-08-28"


def get_genai_client() -> genai.Client:
    """GEMINI_API_KEY로 GenAI 클라이언트를 생성한다."""
    return genai.Client(api_key=gemini_api_key)


def get_genai_error_detail(exc: Exception) -> tuple[int, str]:
    """GenAI SDK 예외를 웹 UI에 표시할 HTTP 상태와 메시지로 변환한다.

    Args:
        exc: Google GenAI SDK 호출 중 발생한 예외.

    Returns:
        HTTP 상태 코드와 사용자에게 표시할 오류 메시지 튜플.
    """
    message = str(exc)
    if "API_KEY_INVALID" in message or "API key not valid" in message:
        return 401, "API key not valid. Please pass a valid API key."
    return 500, message


def is_transient_genai_error(exc: Exception) -> bool:
    """GenAI 요청을 재시도해도 되는 일시적인 전송/서버 오류인지 확인한다."""
    if isinstance(exc, (ConnectionError, TimeoutError)):
        return True
    message = str(exc).lower()
    transient_markers = (
        "server disconnected",
        "connection reset",
        "connection aborted",
        "remote protocol error",
        "timeout",
        "timed out",
        "temporarily unavailable",
        "service unavailable",
        " 502",
        " 503",
        " 504",
    )
    return any(marker in message for marker in transient_markers)


def call_with_transient_retry(label: str, function, max_attempts: int = 5):
    """멱등적인 동기 GenAI 조회를 일시적인 전송 오류에 재시도한다."""
    for attempt in range(max_attempts):
        try:
            return function()
        except Exception as exc:
            if not is_transient_genai_error(exc) or attempt == max_attempts - 1:
                raise
            retry_delay = min(2 ** attempt, 15)
            logger.warning(
                "Transient %s error (attempt %s/%s, retrying in %ss): %s",
                label,
                attempt + 1,
                max_attempts,
                retry_delay,
                str(exc)[:160],
            )
            time.sleep(retry_delay)
    raise RuntimeError(f"{label} 재시도 횟수를 초과했습니다.")


async def wait_for_video_operation(client, operation, poll_interval: float = 10) -> object:
    """장기 실행 비디오 operation을 일시적인 조회 오류에 복원력 있게 대기한다."""
    max_attempts = 5
    operation_name = getattr(operation, "name", "unknown")
    logger.info("Waiting for video operation: %s", operation_name)
    while not operation.done:
        await asyncio.sleep(poll_interval)
        for attempt in range(max_attempts):
            try:
                operation = client.operations.get(operation)
                break
            except Exception as exc:
                if not is_transient_genai_error(exc) or attempt == max_attempts - 1:
                    raise
                retry_delay = min(2 ** attempt, 15)
                logger.warning(
                    "Transient video operation polling error "
                    "(attempt %s/%s, retrying in %ss): %s",
                    attempt + 1,
                    max_attempts,
                    retry_delay,
                    str(exc)[:160],
                )
                await asyncio.sleep(retry_delay)
    logger.info("Video operation completed: %s", operation_name)
    return operation


async def download_video_with_retry(client, video_file, max_attempts: int = 5) -> bytes:
    """완료된 비디오 파일을 일시적인 전송 오류에 복원력 있게 다운로드한다."""
    logger.info("Downloading generated video file...")
    for attempt in range(max_attempts):
        try:
            video_bytes = await asyncio.to_thread(client.files.download, file=video_file)
            logger.info("Generated video download completed: %s bytes", len(video_bytes))
            return video_bytes
        except Exception as exc:
            if not is_transient_genai_error(exc) or attempt == max_attempts - 1:
                raise
            retry_delay = min(2 ** attempt, 15)
            logger.warning(
                "Transient video download error "
                "(attempt %s/%s, retrying in %ss): %s",
                attempt + 1,
                max_attempts,
                retry_delay,
                str(exc)[:160],
            )
            await asyncio.sleep(retry_delay)
    raise RuntimeError("비디오 다운로드 재시도 횟수를 초과했습니다.")


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

# 장시간 실행되는 비디오 작업 저장소 (메모리)
# 리버스 프록시의 요청 타임아웃을 피하기 위해 생성 요청과 결과 조회를 분리한다.
video_jobs = {}
video_job_tasks = set()


async def run_video_job(job_id: str, operation) -> None:
    """비디오 작업 코루틴을 실행하고 조회 가능한 상태로 결과를 저장한다.

    Args:
        job_id: 클라이언트에 반환된 비디오 작업 UUID.
        operation: 기존 비디오 생성 또는 확장 엔드포인트의 코루틴.
    """
    video_jobs[job_id] = {"status": "running", "created_at": time.time()}
    try:
        response = await operation
        result = json.loads(response.body.decode("utf-8"))
        video_jobs[job_id] = {
            "status": "success",
            "result": result,
            "created_at": video_jobs[job_id]["created_at"],
            "completed_at": time.time(),
        }
    except HTTPException as exc:
        video_jobs[job_id] = {
            "status": "error",
            "detail": exc.detail,
            "created_at": video_jobs[job_id]["created_at"],
            "completed_at": time.time(),
        }
    except Exception as exc:
        logger.exception("Video background job failed: %s", job_id)
        video_jobs[job_id] = {
            "status": "error",
            "detail": str(exc),
            "created_at": video_jobs[job_id]["created_at"],
            "completed_at": time.time(),
        }


def start_video_job(operation) -> str:
    """비디오 작업을 이벤트 루프에 등록하고 작업 UUID를 반환한다."""
    job_id = str(uuid.uuid4())
    video_jobs[job_id] = {"status": "queued", "created_at": time.time()}
    task = asyncio.create_task(run_video_job(job_id, operation))
    video_job_tasks.add(task)
    task.add_done_callback(video_job_tasks.discard)
    return job_id

# 이미지 채팅 세션 저장소 (메모리)
# Gemini 세션은 interaction을 생성한 API 키에 종속되므로 같은 client를 계속 사용한다.
# session_id -> {"interaction_id": str, "client": client, "model": str} 매핑
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


def _generate_omni_video_sync(
    prompt: str,
    resolution: str,
    aspect_ratio: str,
    input_images: Optional[list[tuple[bytes, str]]] = None,
    previous_interaction_id: Optional[str] = None,
) -> tuple[str, bytes]:
    """Interactions API로 Gemini Omni 동영상을 생성하고 MP4 바이트를 반환한다.

    Args:
        prompt: 생성할 동영상에 대한 텍스트 지시문.
        resolution: 출력 해상도. 360p, 720p, 1080p 또는 4k.
        aspect_ratio: 출력 화면비. 16:9 또는 9:16.
        input_images: 이미지-동영상 생성에 사용할 이미지 바이트와 MIME 타입 목록.

    Returns:
        생성된 MP4 파일 바이트.

    Raises:
        RuntimeError: API 응답에 다운로드 가능한 동영상이 없는 경우.
    """
    client = get_genai_client()
    interaction_input: str | list[dict] = prompt
    if input_images:
        interaction_input = [
            {
                "type": "image",
                "data": base64.b64encode(image_bytes).decode("utf-8"),
                "mime_type": mime_type,
            }
            for image_bytes, mime_type in input_images
        ]
        interaction_input.append({"type": "text", "text": prompt})

    response_format = {
        "type": "video",
        "delivery": "uri" if resolution in {"1080p", "4k"} else "inline",
        "aspect_ratio": aspect_ratio,
        "resolution": resolution,
        "duration": f"{VIDEO_DURATIONS_SECONDS[OMNI_MODEL]}s",
    }
    create_kwargs = {
        "model": OMNI_MODEL,
        "input": interaction_input,
        "background": True,
        "extra_body": {"response_format": response_format},
    }
    if previous_interaction_id:
        create_kwargs["previous_interaction_id"] = previous_interaction_id

    interaction = client.interactions.create(**create_kwargs)
    interaction_id = getattr(interaction, "id", None)
    if not interaction_id:
        raise RuntimeError("Gemini Omni 백그라운드 작업 ID를 받지 못했습니다.")
    logger.info("Gemini Omni background interaction started: %s", interaction_id)

    for _ in range(180):
        status = str(getattr(interaction, "status", "")).lower()
        if status == "completed":
            break
        if status in {"failed", "cancelled"}:
            raise RuntimeError(f"Gemini Omni 동영상 생성 작업이 {status} 상태로 종료되었습니다.")
        time.sleep(5)
        interaction = call_with_transient_retry(
            "Omni interaction polling",
            lambda: client.interactions.get(id=interaction_id),
        )
    else:
        raise RuntimeError("Gemini Omni 동영상 생성 시간이 초과되었습니다.")

    logger.info("Gemini Omni background interaction completed: %s", interaction_id)
    output_video = interaction.output_video
    if output_video is None:
        raise RuntimeError("Gemini Omni 응답에 동영상 데이터가 없습니다.")
    if output_video.data:
        return interaction_id, base64.b64decode(output_video.data)
    if output_video.uri:
        file_name = output_video.uri.split("/")[-1]
        for _ in range(120):
            file_info = call_with_transient_retry(
                "Omni video file polling",
                lambda: client.files.get(name=f"files/{file_name}"),
            )
            state = getattr(file_info.state, "name", str(file_info.state))
            if state == "ACTIVE":
                video_bytes = call_with_transient_retry(
                    "Omni video download",
                    lambda: client.files.download(file=output_video.uri),
                )
                return interaction_id, video_bytes
            if state == "FAILED":
                raise RuntimeError("Gemini Omni 동영상 파일 처리에 실패했습니다.")
            time.sleep(5)
        raise RuntimeError("Gemini Omni 동영상 파일 처리 시간이 초과되었습니다.")
    raise RuntimeError("Gemini Omni 응답에 다운로드 가능한 동영상이 없습니다.")


async def generate_omni_video(
    prompt: str,
    resolution: str,
    aspect_ratio: str,
    input_images: Optional[list[tuple[bytes, str]]] = None,
) -> JSONResponse:
    """Omni 동영상 생성을 작업 스레드에서 실행하고 기존 API 형식으로 반환한다."""
    interaction_id, video_bytes = await asyncio.to_thread(
        _generate_omni_video_sync,
        prompt,
        resolution,
        aspect_ratio,
        input_images,
    )
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    output_filename = f"output_{timestamp}.mp4"
    (OUTPUTS_DIR / output_filename).write_bytes(video_bytes)
    video_uuid = str(uuid.uuid4())
    video_objects_cache[video_uuid] = {
        "interaction_id": interaction_id,
        "model": OMNI_MODEL,
        "edit_count": 0,
    }
    logger.info("Gemini Omni video saved", extra={"output_filename": output_filename})
    return JSONResponse({
        "status": "success",
        "message": "비디오가 생성되었습니다.",
        "output_file": f"/outputs/{output_filename}",
        "video_uuid": video_uuid,
        "model": OMNI_MODEL,
        "extension_count": 0,
        "can_extend": True,
        "cumulative_duration_seconds": VIDEO_DURATIONS_SECONDS[OMNI_MODEL],
    })

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
    # thought_signature 오류가 발생할 수 있어 짧은 백오프로 재시도한다.
    # not_found는 API 키 불일치 또는 만료된 interaction을 뜻하므로 재시도하지 않는다.
    is_continuation = previous_interaction_id is not None
    max_attempts = 3 if is_continuation else 1
    interaction = None
    for attempt in range(max_attempts):
        try:
            interaction = client.interactions.create(**body)
            break
        except Exception as exc:
            message = str(exc)
            transient = "thought_signature" in message
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
VIDEO_THUMBNAIL_SEMAPHORE = asyncio.Semaphore(1)
MIN_VIDEO_FILE_SIZE = 1024


class GalleryCache(BaseModel):
    """프로세스 내 갤러리 파일 인덱스 캐시 상태."""

    directory_mtime_ns: Optional[int] = None
    images: list[dict] = []


GALLERY_CACHE = GalleryCache()


def invalidate_gallery_cache() -> None:
    """다음 갤러리 조회에서 파일 인덱스를 다시 생성하도록 캐시를 무효화한다."""
    GALLERY_CACHE.directory_mtime_ns = None


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
        invalidate_gallery_cache()
        logger.info(f"Thumbnail created: {thumb_path.name}")
        return thumb_path
    except (OSError, ValueError) as exc:
        logger.warning(f"Thumbnail creation failed for {original_path.name}: {exc}")
        return None


def create_video_thumbnail(original_path: Path) -> Optional[Path]:
    """ffmpeg로 비디오의 대표 프레임 썸네일을 생성한다."""
    ffmpeg_path = shutil.which("ffmpeg")
    if not ffmpeg_path:
        logger.warning("Video thumbnail skipped because ffmpeg is unavailable")
        return None

    thumb_path = original_path.with_name(original_path.name + THUMBNAIL_SUFFIX)
    try:
        subprocess.run(
            [
                ffmpeg_path,
                "-y",
                "-ss", "0.5",
                "-i", str(original_path),
                "-frames:v", "1",
                "-vf",
                f"scale={THUMBNAIL_MAX_SIZE}:{THUMBNAIL_MAX_SIZE}:force_original_aspect_ratio=decrease",
                "-f", "image2",
                "-vcodec", "png",
                str(thumb_path),
            ],
            check=True,
            capture_output=True,
            timeout=30,
        )
        invalidate_gallery_cache()
        logger.info("Video thumbnail created: %s", thumb_path.name)
        return thumb_path
    except (OSError, subprocess.SubprocessError) as exc:
        thumb_path.unlink(missing_ok=True)
        logger.warning("Video thumbnail creation failed for %s: %s", original_path.name, exc)
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
        "video_omni_model": OMNI_MODEL,
        "video_default_model": VEO_DEFAULT_MODEL,
        "video_model_aliases": VEO_MODELS,
        "pricing": {
            "image_output": IMAGE_OUTPUT_PRICES,
            "video_per_second": VIDEO_PRICES_PER_SECOND,
            "video_durations_seconds": VIDEO_DURATIONS_SECONDS,
            "omni": {
                "model": OMNI_MODEL,
                "input_per_million_tokens": OMNI_INPUT_PRICE_PER_MILLION_TOKENS,
                "text_output_per_million_tokens": OMNI_TEXT_OUTPUT_PRICE_PER_MILLION_TOKENS,
                "video_output_per_million_tokens": OMNI_VIDEO_OUTPUT_PRICE_PER_MILLION_TOKENS,
                "tokens_per_second_720p": OMNI_720P_TOKENS_PER_SECOND,
            },
            "tts": {
                "model": TTS_MODEL,
                "input_per_million_tokens": TTS_INPUT_PRICE_PER_MILLION_TOKENS,
                "output_per_million_tokens": TTS_OUTPUT_PRICE_PER_MILLION_TOKENS,
                "audio_tokens_per_second": TTS_AUDIO_TOKENS_PER_SECOND,
            },
            "updated_at": PRICING_UPDATED_AT,
            "source_url": "https://ai.google.dev/gemini-api/docs/pricing",
        },
    })

def get_gallery_images() -> list[dict]:
    """갤러리 미디어 인덱스를 디렉터리 변경 시에만 다시 생성하여 반환한다."""
    directory_mtime_ns = OUTPUTS_DIR.stat().st_mtime_ns
    if GALLERY_CACHE.directory_mtime_ns == directory_mtime_ns:
        return GALLERY_CACHE.images

    images = []
    indexed_filenames = set()
    for thumb_path in OUTPUTS_DIR.glob(f"*{THUMBNAIL_SUFFIX}"):
        # 썸네일명 output_xxx.png.thumb -> 원본명 output_xxx.png
        original_path = thumb_path.with_suffix("")
        if not original_path.exists():
            continue
        media_type = "video" if original_path.suffix.lower() == ".mp4" else "image"
        if media_type == "video" and original_path.stat().st_size < MIN_VIDEO_FILE_SIZE:
            continue
        images.append({
            "filename": original_path.name,
            "thumb_url": f"/api/thumbnail/{thumb_path.name}",
            "original_url": f"/outputs/{original_path.name}",
            "media_type": media_type,
            "mtime": original_path.stat().st_mtime,
        })
        indexed_filenames.add(original_path.name)

    for original_path in OUTPUTS_DIR.glob("*.mp4"):
        if (
            original_path.name in indexed_filenames
            or original_path.stat().st_size < MIN_VIDEO_FILE_SIZE
        ):
            continue
        images.append({
            "filename": original_path.name,
            "thumb_url": f"/api/thumbnail/{original_path.name}{THUMBNAIL_SUFFIX}",
            "original_url": f"/outputs/{original_path.name}",
            "media_type": "video",
            "mtime": original_path.stat().st_mtime,
        })
    images.sort(key=lambda item: item["mtime"], reverse=True)
    GALLERY_CACHE.images = images
    GALLERY_CACHE.directory_mtime_ns = directory_mtime_ns
    return GALLERY_CACHE.images


# 출력 이미지 갤러리 API
@app.get("/api/gallery")
async def get_gallery(
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=30, ge=1, le=100),
):
    """썸네일이 존재하는 출력 이미지 목록을 최신순으로 나누어 반환한다.

    썸네일(.thumb)이 있는 파일만 대상으로 하므로, 썸네일 도입 이전에 생성된
    과거 이미지는 목록에 포함되지 않는다.

    Args:
        offset: 최신 이미지부터 건너뛸 항목 수.
        limit: 한 번에 반환할 항목 수. 최대 100개로 제한한다.

    Returns:
        images, next_offset, has_more를 포함하는 JSON. images는 mtime
        내림차순(최신이 먼저)으로 정렬된다.
    """
    images = get_gallery_images()
    page = images[offset:offset + limit]
    next_offset = offset + len(page)
    return JSONResponse({
        "images": page,
        "next_offset": next_offset,
        "has_more": next_offset < len(images),
    })


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
    if not thumb_path.name.endswith(THUMBNAIL_SUFFIX):
        raise HTTPException(status_code=404, detail="썸네일을 찾을 수 없습니다.")
    if not thumb_path.exists():
        original_path = thumb_path.with_suffix("")
        if original_path.exists() and original_path.suffix.lower() == ".mp4":
            async with VIDEO_THUMBNAIL_SEMAPHORE:
                if not thumb_path.exists():
                    await asyncio.to_thread(create_video_thumbnail, original_path)
    if not thumb_path.exists():
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

    invalidate_gallery_cache()
    return JSONResponse({"status": "success", "message": "이미지가 삭제되었습니다."})


@app.post("/api/text-to-image")
async def text_to_image(
    prompt: str = Form(...),
    aspect_ratio: str = Form("16:9"),
    model: str = Form(None),
    resolution: str = Form("1K"),
    is_new: bool = Form(True),
    session_id: Optional[str] = Form(None),
):
    """Text to Image 작업 (Interactions API 기반 Multi-turn 지원)."""
    try:
        if model is None:
            model = STANDARD_MODEL
        logger.info(f"Text to Image request - prompt length: {len(prompt)}, aspect_ratio: {aspect_ratio}, model: {model}, resolution: {resolution}, is_new: {is_new}, session_id: {session_id}")
        logger.info(f"Text to Image prompt: {prompt}")

        # Multi-turn 모드: 이전 interaction ID를 이어받아 서버 사이드 상태로 편집
        previous_interaction_id: Optional[str] = None
        continue_session = not is_new and session_id and session_id in image_chat_sessions
        if continue_session:
            session = image_chat_sessions[session_id]
            previous_interaction_id = session.get("interaction_id")
            if not previous_interaction_id:
                raise HTTPException(status_code=400, detail="편집할 이전 세션 정보가 없습니다.")
            client = session.get("client")
            if client is None:
                raise HTTPException(status_code=409, detail="이전 세션을 이어갈 수 없습니다. 새로 만들기를 선택해 주세요.")
            logger.info(f"Continuing interaction: {previous_interaction_id} (session: {session_id})")
        else:
            client = get_genai_client()

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
            "client": client,
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
        status_code, detail = get_genai_error_detail(e)
        raise HTTPException(status_code=status_code, detail=detail)

@app.post("/api/image-to-image")
async def image_to_image(
    prompt: str = Form(...),
    files: list[UploadFile] = File(None),
    model: str = Form(None),
    resolution: str = Form("1K"),
    is_new: bool = Form(True),
    session_id: Optional[str] = Form(None),
):
    """Image to Image 작업 (Interactions API 기반, 멀티 이미지 및 Multi-turn 지원)."""
    try:
        if model is None:
            model = STANDARD_MODEL
        logger.info(f"Image to Image request - model: {model}, resolution: {resolution}, is_new: {is_new}, session_id: {session_id}")
        logger.info(f"Image to Image prompt: {prompt}")

        previous_interaction_id: Optional[str] = None
        input_images: Optional[list[tuple[bytes, str]]] = None
        continue_session = not is_new and session_id and session_id in image_chat_sessions

        if continue_session:
            # Multi-turn 편집: 서버 사이드 상태를 이어받음 (이전 이미지 재전송 불필요)
            session = image_chat_sessions[session_id]
            previous_interaction_id = session.get("interaction_id")
            if not previous_interaction_id:
                raise HTTPException(status_code=400, detail="편집할 이전 세션 정보가 없습니다.")
            client = session.get("client")
            if client is None:
                raise HTTPException(status_code=409, detail="이전 세션을 이어갈 수 없습니다. 새로 만들기를 선택해 주세요.")
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
            client = get_genai_client()

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
            "client": client,
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
        status_code, detail = get_genai_error_detail(e)
        raise HTTPException(status_code=status_code, detail=detail)

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
        if model == OMNI_MODEL:
            return await generate_omni_video(prompt, resolution, aspect_ratio)

        client = get_genai_client()
        operation = client.models.generate_videos(
            model=model,
            source=types.GenerateVideosSource(prompt=prompt),
            config=types.GenerateVideosConfig(
                resolution=resolution,
                aspect_ratio=aspect_ratio
            )
        )
        
        # 작업 완료 대기
        operation = await wait_for_video_operation(client, operation)
        
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
        
        video_bytes = await download_video_with_retry(client, generated_video.video)
        output_path.write_bytes(video_bytes)
        
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


@app.post("/api/video-jobs/text-to-video", status_code=202)
async def start_text_to_video_job(
    prompt: str = Form(...),
    model: str = Form(VEO_DEFAULT_MODEL),
    resolution: str = Form("720p"),
    aspect_ratio: str = Form("16:9"),
):
    """Text to Video 작업을 백그라운드에서 시작하고 즉시 작업 ID를 반환한다."""
    validate_veo_options(model, resolution)
    job_id = start_video_job(text_to_video(prompt, model, resolution, aspect_ratio))
    return JSONResponse({"status": "queued", "job_id": job_id}, status_code=202)

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

        # Omni는 참조 이미지 6장, Veo는 최대 3장까지 처리한다.
        max_files = 6 if model == OMNI_MODEL else 3
        files_to_process = files[:max_files]
        if model == VEO_LITE_MODEL and len(files_to_process) > 1:
            raise HTTPException(
                status_code=400,
                detail="Veo 3.1 Lite는 시작 이미지 1장만 지원합니다. 여러 참조 이미지는 Standard 또는 Fast를 선택하세요.",
            )
        if model == OMNI_MODEL:
            input_images = await read_upload_images(files_to_process)
            return await generate_omni_video(
                prompt or "Animate this image",
                resolution,
                aspect_ratio,
                input_images,
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
                source=types.GenerateVideosSource(
                    prompt=prompt,
                    image=safe_image,
                ),
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
                source=types.GenerateVideosSource(prompt=prompt),
                config=types.GenerateVideosConfig(
                    reference_images=reference_images,
                    resolution=resolution,
                    aspect_ratio=aspect_ratio
                )
            )
        
        # 작업 완료 대기
        operation = await wait_for_video_operation(client, operation)
        
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
        video_bytes = await download_video_with_retry(client, video.video)
        
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        output_filename = f"output_{timestamp}.mp4"
        output_path = OUTPUTS_DIR / output_filename
        
        output_path.write_bytes(video_bytes)
        
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
        logger.exception("Image to Video error: %s", e)
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
            model = cached_video["model"]
            previous_video = cached_video.get("video")
        else:
            # 서버 재시작 전 형식과의 호환성 유지
            previous_video = cached_video
            model = VEO_STANDARD_MODEL
        logger.info(f"Retrieved video object from cache: {video_uuid}")

        if model == OMNI_MODEL:
            previous_interaction_id = cached_video.get("interaction_id")
            if not previous_interaction_id:
                raise HTTPException(status_code=400, detail="Gemini Omni 편집 세션을 찾을 수 없습니다.")
            extension_count = cached_video.get("edit_count", 0)
            if extension_count >= OMNI_MAX_VIDEO_EXTENSIONS:
                raise HTTPException(
                    status_code=400,
                    detail="Gemini Omni 비디오는 최대 누적 40초까지만 연장할 수 있습니다.",
                )

            interaction_id, video_bytes = await asyncio.to_thread(
                _generate_omni_video_sync,
                prompt,
                resolution,
                aspect_ratio,
                None,
                previous_interaction_id,
            )
            timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            output_filename = f"output_{timestamp}.mp4"
            (OUTPUTS_DIR / output_filename).write_bytes(video_bytes)

            edited_video_uuid = str(uuid.uuid4())
            extension_count += 1
            video_objects_cache[edited_video_uuid] = {
                "interaction_id": interaction_id,
                "model": OMNI_MODEL,
                "edit_count": extension_count,
            }
            del video_objects_cache[video_uuid]
            return JSONResponse({
                "status": "success",
                "message": "Gemini Omni 비디오가 10초 연장되었습니다.",
                "output_file": f"/outputs/{output_filename}",
                "video_uuid": edited_video_uuid,
                "model": OMNI_MODEL,
                "extension_count": extension_count,
                "can_extend": extension_count < OMNI_MAX_VIDEO_EXTENSIONS,
                "cumulative_duration_seconds": (
                    extension_count + 1
                ) * VIDEO_DURATIONS_SECONDS[OMNI_MODEL],
            })

        if model == VEO_LITE_MODEL:
            raise HTTPException(status_code=400, detail="Veo 3.1 Lite로 생성한 비디오는 확장할 수 없습니다.")
        if resolution != "720p":
            raise HTTPException(status_code=400, detail="비디오 확장은 720p 해상도만 지원합니다.")

        client = get_genai_client()
        
        # 비디오 확장 작업 시작 (previous_video.video 전달)
        operation = client.models.generate_videos(
            model=model,
            source=types.GenerateVideosSource(
                prompt=prompt,
                video=previous_video.video,
            ),
            config=types.GenerateVideosConfig(
                number_of_videos=1,
                resolution=resolution,
                aspect_ratio=aspect_ratio
            )
        )
        
        logger.info("Video extension operation started")
        
        # 작업 완료 대기
        operation = await wait_for_video_operation(client, operation)
        
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
        
        video_bytes = await download_video_with_retry(client, generated_video.video)
        output_path.write_bytes(video_bytes)
        
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


@app.post("/api/video-jobs/image-to-video", status_code=202)
async def start_image_to_video_job(
    prompt: str = Form(...),
    files: list[UploadFile] = File(...),
    model: str = Form(VEO_DEFAULT_MODEL),
    resolution: str = Form("720p"),
    aspect_ratio: str = Form("16:9"),
):
    """Image to Video 작업을 백그라운드에서 시작하고 즉시 작업 ID를 반환한다.

    요청 종료 후 원본 UploadFile이 닫히므로 파일 내용을 독립적인 메모리 스트림으로
    복사한 뒤 백그라운드 작업에 전달한다.
    """
    validate_veo_options(model, resolution)
    copied_files = []
    max_files = 6 if model == OMNI_MODEL else 3
    for file in files[:max_files]:
        content = await file.read()
        headers = Headers({"content-type": file.content_type or "image/png"})
        copied_files.append(UploadFile(file=BytesIO(content), filename=file.filename, headers=headers))

    job_id = start_video_job(
        image_to_video(prompt, copied_files, model, resolution, aspect_ratio)
    )
    return JSONResponse({"status": "queued", "job_id": job_id}, status_code=202)


@app.post("/api/video-jobs/extend-video", status_code=202)
async def start_extend_video_job(
    prompt: str = Form(...),
    video_uuid: str = Form(...),
    resolution: str = Form("720p"),
    aspect_ratio: str = Form("16:9"),
):
    """비디오 확장 작업을 백그라운드에서 시작하고 즉시 작업 ID를 반환한다."""
    if video_uuid not in video_objects_cache:
        raise HTTPException(status_code=400, detail="확장할 비디오를 찾을 수 없습니다.")
    job_id = start_video_job(extend_video(prompt, video_uuid, resolution, aspect_ratio))
    return JSONResponse({"status": "queued", "job_id": job_id}, status_code=202)


@app.get("/api/video-jobs/{job_id}")
async def get_video_job(job_id: str):
    """백그라운드 비디오 작업의 현재 상태 또는 최종 결과를 반환한다."""
    job = video_jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="비디오 작업을 찾을 수 없습니다.")
    return JSONResponse(job)

@app.post("/api/text-to-speech")
async def text_to_speech(
    prompt: str = Form(...),
    voice_name: str = Form("Zephyr")
):
    """Text to Speech 작업"""
    try:
        model = TTS_MODEL
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
        host="localhost", 
        port=33000,
        log_level="info",
        access_log=True,
        timeout_graceful_shutdown=10,
    )

