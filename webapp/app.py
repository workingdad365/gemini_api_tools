import os
import time
import sqlite3
import mimetypes
import struct
from datetime import datetime
from io import BytesIO
from typing import Optional
from pathlib import Path

from fastapi import FastAPI, File, UploadFile, Form, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import google.generativeai as genai_old
from google import genai
from google.genai import types
from PIL import Image
from dotenv import load_dotenv

# 환경 변수 로드
load_dotenv()

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
    raise ValueError("GEMINI_API_KEY not found in environment variables")

genai_old.configure(api_key=api_key)
genai_client = genai.Client(api_key=api_key)

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

@app.post("/api/text-to-image")
async def text_to_image(
    prompt: str = Form(...),
    aspect_ratio: str = Form("16:9")
):
    """Text to Image 작업"""
    try:
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
                inline_data = chunk.candidates[0].content.parts[0].inline_data
                data_buffer = inline_data.data
                file_extension = mimetypes.guess_extension(inline_data.mime_type)
                
                output_filename = f"output_{timestamp}{file_extension}"
                output_path = OUTPUTS_DIR / output_filename
                
                with open(output_path, "wb") as f:
                    f.write(data_buffer)
                
                return JSONResponse({
                    "status": "success",
                    "message": "이미지가 생성되었습니다.",
                    "output_file": f"/outputs/{output_filename}"
                })
        
        raise HTTPException(status_code=500, detail="이미지 생성 실패")
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/image-to-image")
async def image_to_image(
    prompt: str = Form(...),
    file: UploadFile = File(...)
):
    """Image to Image 작업"""
    try:
        # 파일 저장
        upload_path = UPLOADS_DIR / file.filename
        with open(upload_path, "wb") as buffer:
            buffer.write(await file.read())
        
        model = genai_old.GenerativeModel('gemini-2.5-flash-image-preview')
        img_to_edit = Image.open(upload_path)
        response = model.generate_content([prompt, img_to_edit])
        
        if response.candidates and response.candidates[0].content.parts:
            for part in response.candidates[0].content.parts:
                if part.inline_data:
                    image_data = BytesIO(part.inline_data.data)
                    img = Image.open(image_data)
                    
                    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
                    output_filename = f"output_{timestamp}.png"
                    output_path = OUTPUTS_DIR / output_filename
                    
                    img.save(output_path)
                    
                    # 업로드된 파일 삭제
                    upload_path.unlink()
                    
                    return JSONResponse({
                        "status": "success",
                        "message": "이미지가 생성되었습니다.",
                        "output_file": f"/outputs/{output_filename}"
                    })
        
        raise HTTPException(status_code=500, detail="이미지 생성 실패")
    
    except Exception as e:
        if upload_path.exists():
            upload_path.unlink()
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/text-to-video")
async def text_to_video(
    background_tasks: BackgroundTasks,
    prompt: str = Form(...),
    resolution: str = Form("720p"),
    aspect_ratio: str = Form("16:9")
):
    """Text to Video 작업 (비동기)"""
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
    background_tasks: BackgroundTasks,
    prompt: str = Form(...),
    file: UploadFile = File(...),
    resolution: str = Form("720p"),
    aspect_ratio: str = Form("16:9")
):
    """Image to Video 작업 (비동기)"""
    try:
        # 파일 저장
        upload_path = UPLOADS_DIR / file.filename
        with open(upload_path, "wb") as buffer:
            buffer.write(await file.read())
        
        # PIL Image 로드 및 바이트로 변환
        pil_image = Image.open(upload_path)
        img_byte_arr = BytesIO()
        
        # MIME 타입 추론
        mime_type = mimetypes.guess_type(upload_path)[0]
        if not mime_type:
            mime_type = "image/png"
        
        # 이미지를 바이트로 변환
        image_format = mime_type.split('/')[-1].upper()
        if image_format == 'JPG':
            image_format = 'JPEG'
        pil_image.save(img_byte_arr, format=image_format)
        image_bytes = img_byte_arr.getvalue()
        
        # types.Image 객체 생성
        safe_image = types.Image(
            image_bytes=image_bytes,
            mime_type=mime_type
        )
        
        model = "veo-3.1-generate-preview"
        
        # 프롬프트가 없으면 기본 프롬프트 사용
        if not prompt:
            prompt = "Animate this image"
        
        operation = genai_client.models.generate_videos(
            model=model,
            prompt=prompt,
            image=safe_image,
            config=types.GenerateVideosConfig(
                resolution=resolution,
                aspect_ratio=aspect_ratio
            )
        )
        
        # 작업 완료 대기
        while not operation.done:
            time.sleep(10)
            operation = genai_client.operations.get(operation)
        
        # 비디오 다운로드
        video = operation.response.generated_videos[0]
        genai_client.files.download(file=video.video)
        
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        output_filename = f"output_{timestamp}.mp4"
        output_path = OUTPUTS_DIR / output_filename
        
        video.video.save(str(output_path))
        
        # 업로드된 파일 삭제
        upload_path.unlink()
        
        return JSONResponse({
            "status": "success",
            "message": "비디오가 생성되었습니다.",
            "output_file": f"/outputs/{output_filename}"
        })
    
    except Exception as e:
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
    uvicorn.run(app, host="0.0.0.0", port=33000)

