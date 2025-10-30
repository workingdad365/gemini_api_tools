import os
import time
import sqlite3
import mimetypes
import struct
import logging
import traceback
from datetime import datetime
from io import BytesIO
from typing import Optional
from pathlib import Path

from fastapi import FastAPI, File, UploadFile, Form, HTTPException
from fastapi.responses import FileResponse, JSONResponse
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

# 환경 변수 로드
load_dotenv()

# .env 파일을 명시적으로 로드 (루트 디렉토리의 .env)
env_path = Path(__file__).resolve().parent.parent / ".env"
if env_path.exists():
    load_dotenv(env_path)
    logger.info(f"Loaded .env from {env_path}")
else:
    logger.warning(f".env file not found at {env_path}")

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
BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
UPLOADS_DIR = BASE_DIR / "uploads"
OUTPUTS_DIR = BASE_DIR / "outputs"
DB_PATH = BASE_DIR / "data.db"  # 웹앱 전용 데이터베이스

# 디렉토리 생성
UPLOADS_DIR.mkdir(exist_ok=True)
OUTPUTS_DIR.mkdir(exist_ok=True)

# API 클라이언트 초기화
api_key = os.getenv("GEMINI_API_KEY")
if not api_key:
    logger.error("GEMINI_API_KEY not found in environment variables")
    raise ValueError("GEMINI_API_KEY not found in environment variables")

logger.info("API key loaded successfully")
genai_client = genai.Client(api_key=api_key)
logger.info("Gemini client initialized successfully")

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

# API 엔드포인트
@app.get("/")
async def read_root():
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
    aspect_ratio: str = Form("16:9")
):
    """Text to Image 작업"""
    try:
        logger.info(f"Text to Image request - prompt length: {len(prompt)}, aspect_ratio: {aspect_ratio}")
        
        model = "gemini-2.5-flash-image"
        contents = [
            types.Content(
                role="user",
                parts=[types.Part.from_text(text=prompt)],
            ),
        ]
        generate_content_config = types.GenerateContentConfig(
            response_modalities=["IMAGE", "TEXT"],
            image_config=types.ImageConfig(aspect_ratio=aspect_ratio),
        )
        
        logger.info("Calling Gemini API...")
        text_response = ""  # 텍스트 응답 누적
        
        for chunk in genai_client.models.generate_content_stream(
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
            
            # 모든 parts를 순회하면서 텍스트와 이미지를 각각 처리
            for part in chunk.candidates[0].content.parts:
                # 텍스트 응답 처리
                if part.text is not None:
                    text_response += part.text
                    logger.info(f"Received text response: {part.text}")
                
                # 이미지 데이터 처리
                elif part.inline_data is not None and part.inline_data.data:
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
                        "output_file": f"/outputs/{output_filename}"
                    }
                    # 텍스트 응답이 있으면 포함
                    if text_response:
                        response_data["llm_response"] = text_response
                    
                    return JSONResponse(response_data)
        
        logger.error("No image data received from API")
        raise HTTPException(status_code=500, detail="이미지 생성 실패")
    
    except Exception as e:
        logger.error(f"Text to Image error: {str(e)}")
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/image-to-image")
async def image_to_image(
    prompt: str = Form(...),
    files: list[UploadFile] = File(...)
):
    """Image to Image 작업 (멀티 이미지 지원)"""
    upload_paths = []
    try:
        # 최대 3개까지만 처리
        files_to_process = files[:3]
        logger.info(f"Processing {len(files_to_process)} images for image-to-image")
        
        # 파일 저장
        for file in files_to_process:
            upload_path = UPLOADS_DIR / file.filename
            with open(upload_path, "wb") as buffer:
                buffer.write(await file.read())
            upload_paths.append(upload_path)
        
        # 안전 필터 설정 (OFF)
        safety_settings = [
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
        
        # 1개 이미지인 경우 gemini-2.5-flash-image-preview 사용
        if len(upload_paths) == 1:
            img_to_edit = Image.open(upload_paths[0])
            model = 'gemini-2.5-flash-image-preview'
            contents = [img_to_edit, prompt]
        else:
            # 2개 이상 이미지인 경우 gemini-2.5-flash-image 사용
            images = [Image.open(path) for path in upload_paths]
            model = 'gemini-2.5-flash-image'
            contents = images + [prompt]
        
        text_response = ""  # 텍스트 응답 누적
        
        # 스트리밍 방식으로 이미지 생성
        for chunk in genai_client.models.generate_content_stream(
            model=model,
            contents=contents,
            config=types.GenerateContentConfig(
                safety_settings=safety_settings
            )
        ):
            if (
                chunk.candidates is None
                or chunk.candidates[0].content is None
                or chunk.candidates[0].content.parts is None
            ):
                continue
            
            # 모든 parts를 순회하면서 텍스트와 이미지를 각각 처리
            for part in chunk.candidates[0].content.parts:
                # 텍스트 응답 처리
                if part.text is not None:
                    text_response += part.text
                    logger.info(f"Received text response: {part.text}")
                
                # 이미지 데이터 처리
                elif part.inline_data is not None and part.inline_data.data:
                    image_data = BytesIO(part.inline_data.data)
                    img = Image.open(image_data)
                    
                    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
                    output_filename = f"output_{timestamp}.png"
                    output_path = OUTPUTS_DIR / output_filename
                    
                    img.save(output_path)
                    
                    # 업로드된 파일 삭제
                    for upload_path in upload_paths:
                        if upload_path.exists():
                            upload_path.unlink()
                    
                    response_data = {
                        "status": "success",
                        "message": "이미지가 생성되었습니다.",
                        "output_file": f"/outputs/{output_filename}"
                    }
                    # 텍스트 응답이 있으면 포함
                    if text_response:
                        response_data["llm_response"] = text_response
                    
                    return JSONResponse(response_data)
        
        logger.error("No image data received from API")
        raise HTTPException(status_code=500, detail="이미지 생성 실패: 응답에 이미지 데이터가 없습니다.")
    
    except Exception as e:
        logger.error(f"Image to Image error: {str(e)}")
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
        operation = genai_client.models.generate_videos(
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
            operation = genai_client.operations.get(operation)
        
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
        
        genai_client.files.download(file=generated_video.video)
        generated_video.video.save(str(output_path))
        
        return JSONResponse({
            "status": "success",
            "message": "비디오가 생성되었습니다.",
            "output_file": f"/outputs/{output_filename}"
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
            
            operation = genai_client.models.generate_videos(
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
            
            operation = genai_client.models.generate_videos(
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
            operation = genai_client.operations.get(operation)
        
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
        genai_client.files.download(file=video.video)
        
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        output_filename = f"output_{timestamp}.mp4"
        output_path = OUTPUTS_DIR / output_filename
        
        video.video.save(str(output_path))
        
        # 업로드된 파일 삭제
        for upload_path in upload_paths:
            if upload_path.exists():
                upload_path.unlink()
        
        return JSONResponse({
            "status": "success",
            "message": "비디오가 생성되었습니다.",
            "output_file": f"/outputs/{output_filename}"
        })
    
    except Exception as e:
        logger.error(f"Image to Video error: {str(e)}")
        for upload_path in upload_paths:
            if upload_path.exists():
                upload_path.unlink()
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
        
        for chunk in genai_client.models.generate_content_stream(
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

