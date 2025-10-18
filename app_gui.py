import os
import time
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
from datetime import datetime
from io import BytesIO
from threading import Thread
import mimetypes
import struct
import subprocess
import platform
import sqlite3

from google import genai
from google.genai import types
from PIL import Image
from dotenv import load_dotenv

# tkinterdnd2 선택적 임포트
try:
    from tkinterdnd2 import TkinterDnD, DND_FILES
    DRAG_DROP_AVAILABLE = True
except ImportError:
    DRAG_DROP_AVAILABLE = False


class GoogleAPIToolsGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Google Gemini API Tools")
        self.root.geometry("900x800")
        self.root.resizable(True, True)
        self.drag_drop_enabled = DRAG_DROP_AVAILABLE
        
        # 환경 설정 로드
        load_dotenv()
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            messagebox.showerror("Error", "GEMINI_API_KEY not found in .env file")
            return
        
        self.genai_client = genai.Client(api_key=api_key)
        
        # 데이터베이스 초기화
        self.init_database()
        
        # 변수 초기화
        self.input_file_path = tk.StringVar()
        self.output_directory = tk.StringVar(value=os.path.join(os.getcwd(), "output"))
        self.operation_type = tk.StringVar(value="Text to Image")
        self.aspect_ratio = tk.StringVar(value="16:9")
        self.video_resolution = tk.StringVar(value="720p")
        self.video_aspect_ratio = tk.StringVar(value="16:9")
        self.voice_name = tk.StringVar(value="Zephyr -- Bright")
        self.current_prompt_id = None  # 현재 선택된 프롬프트 ID
        
        self.create_menu()
        self.create_widgets()
        self.update_input_file_state()
    
    def init_database(self):
        """SQLite 데이터베이스 초기화"""
        self.db_path = "data.db"
        conn = sqlite3.connect(self.db_path)
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
    
    def create_menu(self):
        """메뉴바 생성"""
        menubar = tk.Menu(self.root)
        self.root.config(menu=menubar)
        
        # 파일 메뉴
        file_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="파일", menu=file_menu)
        file_menu.add_command(label="출력 디렉토리 열기", command=self.open_output_directory)
        file_menu.add_separator()
        file_menu.add_command(label="종료", command=self.root.quit)
        
        # 프롬프트 메뉴
        prompt_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="프롬프트", menu=prompt_menu)
        prompt_menu.add_command(label="새로 저장", command=self.save_new_prompt)
        prompt_menu.add_command(label="수정", command=self.update_prompt)
        prompt_menu.add_command(label="삭제", command=self.delete_prompt)
        prompt_menu.add_separator()
        prompt_menu.add_command(label="프롬프트 목록", command=self.show_prompt_list)
        
        # 도움말 메뉴
        help_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="도움말", menu=help_menu)
        help_menu.add_command(label="정보", command=self.show_about)
        
    def create_widgets(self):
        # 메인 프레임
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        main_frame.columnconfigure(1, weight=1)
        
        row = 0
        
        # 작업 유형 선택
        ttk.Label(main_frame, text="작업 선택:", font=("", 10, "bold")).grid(
            row=row, column=0, sticky=tk.W, pady=5
        )
        row += 1
        
        operation_combo = ttk.Combobox(
            main_frame, 
            textvariable=self.operation_type,
            values=[
                "Text to Image",
                "Image to Image",
                "Text to Video",
                "Image to Video",
                "Text to Speech"
            ],
            state="readonly",
            width=30
        )
        operation_combo.grid(row=row, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=5)
        operation_combo.bind("<<ComboboxSelected>>", lambda e: self.update_input_file_state())
        row += 1
        
        # 구분선
        ttk.Separator(main_frame, orient='horizontal').grid(
            row=row, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=10
        )
        row += 1
        
        # 입력 파일 섹션
        ttk.Label(main_frame, text="입력 파일 (옵션):", font=("", 10, "bold")).grid(
            row=row, column=0, sticky=tk.W, pady=5
        )
        row += 1
        
        # 드래그앤드랍 영역 (프레임으로 감싸서 드래그 영역 확대)
        self.drop_frame = tk.Frame(main_frame, relief=tk.SUNKEN, borderwidth=1, bg='white')
        self.drop_frame.grid(row=row, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=5)
        
        # 드래그앤드랍 안내 레이블 (프레임 내부)
        drag_text = "여기에 파일을 드래그앤드랍하거나 '찾아보기' 버튼을 클릭하세요" if self.drag_drop_enabled else "'찾아보기' 버튼을 클릭하여 파일을 선택하세요"
        self.drag_label = tk.Label(
            self.drop_frame,
            text=drag_text,
            foreground="gray",
            bg='white',
            pady=5
        )
        self.drag_label.pack(fill=tk.X, padx=5)
        
        self.input_frame = tk.Frame(self.drop_frame, bg='white')
        self.input_frame.pack(fill=tk.X, padx=5, pady=5)
        
        self.input_entry = ttk.Entry(self.input_frame, textvariable=self.input_file_path)
        self.input_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
        
        self.browse_button = ttk.Button(
            self.input_frame, text="찾아보기...", command=self.browse_input_file
        )
        self.browse_button.pack(side=tk.LEFT)
        
        # 드래그앤드랍 이벤트 바인딩 (tkinterdnd2가 설치된 경우)
        if self.drag_drop_enabled:
            # 여러 위젯에 드래그앤드랍 등록
            self.drop_frame.drop_target_register(DND_FILES)
            self.drop_frame.dnd_bind('<<Drop>>', self.on_drop)
            self.drop_frame.dnd_bind('<<DropEnter>>', self.on_drop_enter)
            self.drop_frame.dnd_bind('<<DropLeave>>', self.on_drop_leave)
            
            self.drag_label.drop_target_register(DND_FILES)
            self.drag_label.dnd_bind('<<Drop>>', self.on_drop)
            
            self.input_entry.drop_target_register(DND_FILES)
            self.input_entry.dnd_bind('<<Drop>>', self.on_drop)
        
        row += 1
        
        # 구분선
        ttk.Separator(main_frame, orient='horizontal').grid(
            row=row, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=10
        )
        row += 1
        
        # 이미지 비율 선택 (Image 생성 작업에만 적용)
        aspect_frame = ttk.Frame(main_frame)
        aspect_frame.grid(row=row, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=5)
        
        ttk.Label(aspect_frame, text="이미지 비율:", font=("", 10, "bold")).grid(
            row=0, column=0, sticky=tk.W, padx=(0, 10)
        )
        
        aspect_combo = ttk.Combobox(
            aspect_frame,
            textvariable=self.aspect_ratio,
            values=["1:1", "2:3", "3:2", "3:4", "4:3", "9:16", "16:9", "21:9"],
            state="readonly",
            width=10
        )
        aspect_combo.grid(row=0, column=1, sticky=tk.W)
        
        ttk.Label(aspect_frame, text="(Text to Image 작업에만 적용)", foreground="gray").grid(
            row=0, column=2, sticky=tk.W, padx=(10, 0)
        )
        row += 1
        
        # 비디오 설정 (Video 생성 작업에만 적용)
        video_frame = ttk.Frame(main_frame)
        video_frame.grid(row=row, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=5)
        
        ttk.Label(video_frame, text="비디오 해상도:", font=("", 10, "bold")).grid(
            row=0, column=0, sticky=tk.W, padx=(0, 10)
        )
        
        resolution_combo = ttk.Combobox(
            video_frame,
            textvariable=self.video_resolution,
            values=["720p", "1080p"],
            state="readonly",
            width=10
        )
        resolution_combo.grid(row=0, column=1, sticky=tk.W, padx=(0, 20))
        
        ttk.Label(video_frame, text="비디오 비율:", font=("", 10, "bold")).grid(
            row=0, column=2, sticky=tk.W, padx=(0, 10)
        )
        
        video_aspect_combo = ttk.Combobox(
            video_frame,
            textvariable=self.video_aspect_ratio,
            values=["16:9", "9:16"],
            state="readonly",
            width=10
        )
        video_aspect_combo.grid(row=0, column=3, sticky=tk.W)
        
        ttk.Label(video_frame, text="(Video 생성 작업에만 적용)", foreground="gray").grid(
            row=0, column=4, sticky=tk.W, padx=(10, 0)
        )
        row += 1
        
        # TTS 설정 (Text to Speech 작업에만 적용)
        tts_frame = ttk.Frame(main_frame)
        tts_frame.grid(row=row, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=5)
        
        ttk.Label(tts_frame, text="음성 선택:", font=("", 10, "bold")).grid(
            row=0, column=0, sticky=tk.W, padx=(0, 10)
        )
        
        voice_combo = ttk.Combobox(
            tts_frame,
            textvariable=self.voice_name,
            values=[
                "Zephyr -- Bright",
                "Puck -- Upbeat",
                "Charon -- Informative",
                "Kore -- Firm",
                "Fenrir -- Excitable",
                "Leda -- Youthful",
                "Orus -- Firm",
                "Aoede -- Breezy",
                "Callirrhoe -- Easy-going",
                "Autonoe -- Bright",
                "Enceladus -- Breathy",
                "Iapetus -- Clear",
                "Umbriel -- Easy-going",
                "Algieba -- Smooth",
                "Despina -- Smooth",
                "Erinome -- Clear",
                "Algenib -- Gravelly",
                "Rasalgethi -- Informative",
                "Laomedeia -- Upbeat",
                "Achernar -- Soft",
                "Alnilam -- Firm",
                "Schedar -- Even",
                "Gacrux -- Mature",
                "Pulcherrima -- Forward",
                "Achird -- Friendly",
                "Zubenelgenubi -- Casual",
                "Vindemiatrix -- Gentle",
                "Sadachbia -- Lively",
                "Sadaltager -- Knowledgeable",
                "Sulafat -- Warm"
            ],
            state="readonly",
            width=30
        )
        voice_combo.grid(row=0, column=1, sticky=tk.W)
        
        ttk.Label(tts_frame, text="(Text to Speech 작업에만 적용)", foreground="gray").grid(
            row=0, column=2, sticky=tk.W, padx=(10, 0)
        )
        row += 1
        
        # 구분선
        ttk.Separator(main_frame, orient='horizontal').grid(
            row=row, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=10
        )
        row += 1
        
        # 프롬프트 입력
        prompt_header_frame = ttk.Frame(main_frame)
        prompt_header_frame.grid(row=row, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=5)
        
        ttk.Label(prompt_header_frame, text="프롬프트 입력:", font=("", 10, "bold")).pack(side=tk.LEFT)
        
        ttk.Button(
            prompt_header_frame, text="목록", command=self.show_prompt_list, width=8
        ).pack(side=tk.RIGHT, padx=2)
        
        ttk.Button(
            prompt_header_frame, text="저장", command=self.save_new_prompt, width=8
        ).pack(side=tk.RIGHT, padx=2)
        
        row += 1
        
        self.prompt_text = scrolledtext.ScrolledText(
            main_frame, height=10, wrap=tk.WORD
        )
        self.prompt_text.grid(row=row, column=0, columnspan=2, sticky=(tk.W, tk.E, tk.N, tk.S), pady=5)
        main_frame.rowconfigure(row, weight=1)
        row += 1
        
        # 구분선
        ttk.Separator(main_frame, orient='horizontal').grid(
            row=row, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=10
        )
        row += 1
        
        # 출력 디렉토리
        ttk.Label(main_frame, text="출력 디렉토리:", font=("", 10, "bold")).grid(
            row=row, column=0, sticky=tk.W, pady=5
        )
        row += 1
        
        output_frame = ttk.Frame(main_frame)
        output_frame.grid(row=row, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=5)
        output_frame.columnconfigure(0, weight=1)
        
        ttk.Entry(output_frame, textvariable=self.output_directory).grid(
            row=0, column=0, sticky=(tk.W, tk.E), padx=(0, 5)
        )
        ttk.Button(
            output_frame, text="찾아보기...", command=self.browse_output_directory
        ).grid(row=0, column=1)
        row += 1
        
        # 구분선
        ttk.Separator(main_frame, orient='horizontal').grid(
            row=row, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=10
        )
        row += 1
        
        # 실행 버튼
        self.execute_button = ttk.Button(
            main_frame, text="실행", command=self.execute_operation, style="Accent.TButton"
        )
        self.execute_button.grid(row=row, column=0, columnspan=2, pady=10)
        row += 1
        
        # 진행 상황 표시
        self.progress_label = ttk.Label(main_frame, text="", foreground="blue")
        self.progress_label.grid(row=row, column=0, columnspan=2, pady=5)
        row += 1
        
        # 로그 출력
        ttk.Label(main_frame, text="로그:", font=("", 10, "bold")).grid(
            row=row, column=0, sticky=tk.W, pady=5
        )
        row += 1
        
        self.log_text = scrolledtext.ScrolledText(
            main_frame, height=8, wrap=tk.WORD, state=tk.DISABLED
        )
        self.log_text.grid(row=row, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=5)
        
    def update_input_file_state(self):
        """작업 유형에 따라 입력 파일 섹션 활성화/비활성화"""
        operation = self.operation_type.get()
        needs_input = operation in ["Image to Image", "Image to Video"]
        
        state = "normal" if needs_input else "disabled"
        self.input_entry.config(state=state)
        self.browse_button.config(state=state)
        
        if needs_input:
            self.drag_label.config(foreground="gray", bg="white")
            self.drop_frame.config(bg="white")
            self.input_frame.config(bg="white")
        else:
            self.drag_label.config(foreground="lightgray", bg="#f0f0f0")
            self.drop_frame.config(bg="#f0f0f0")
            self.input_frame.config(bg="#f0f0f0")
            self.input_file_path.set("")
    
    def browse_input_file(self):
        """입력 파일 선택 다이얼로그"""
        filetypes = [
            ("Image files", "*.png *.jpg *.jpeg *.webp *.gif"),
            ("Video files", "*.mp4 *.avi *.mov"),
            ("All files", "*.*")
        ]
        filename = filedialog.askopenfilename(
            title="입력 파일 선택",
            filetypes=filetypes
        )
        if filename:
            self.input_file_path.set(filename)
    
    def browse_output_directory(self):
        """출력 디렉토리 선택 다이얼로그"""
        directory = filedialog.askdirectory(
            title="출력 디렉토리 선택",
            initialdir=self.output_directory.get()
        )
        if directory:
            self.output_directory.set(directory)
    
    def on_drop(self, event):
        """드래그앤드랍 이벤트 핸들러"""
        # 입력 파일이 필요한 작업인지 확인
        operation = self.operation_type.get()
        if operation not in ["Image to Image", "Image to Video"]:
            return
        
        files = self.root.tk.splitlist(event.data)
        if files:
            # 파일 경로에서 중괄호 제거 (Windows에서 발생할 수 있음)
            file_path = files[0].strip('{}').strip('"').strip("'")
            
            # 파일 존재 확인
            if os.path.exists(file_path):
                self.input_file_path.set(file_path)
                self.log(f"파일 드래그앤드랍: {os.path.basename(file_path)}")
                # 원래 배경색으로 복귀
                self.drop_frame.config(bg="white")
                self.drag_label.config(bg="white")
            else:
                self.log(f"파일을 찾을 수 없음: {file_path}")
    
    def on_drop_enter(self, event):
        """드래그 진입 시 시각적 피드백"""
        operation = self.operation_type.get()
        if operation in ["Image to Image", "Image to Video"]:
            self.drop_frame.config(bg="#e8f4ff")
            self.drag_label.config(bg="#e8f4ff")
    
    def on_drop_leave(self, event):
        """드래그 이탈 시 원래 색상으로 복귀"""
        operation = self.operation_type.get()
        if operation in ["Image to Image", "Image to Video"]:
            self.drop_frame.config(bg="white")
            self.drag_label.config(bg="white")
    
    def open_output_directory(self):
        """출력 디렉토리를 파일 탐색기로 열기"""
        output_dir = self.output_directory.get()
        
        try:
            system = platform.system()
            if system == "Windows":
                os.startfile(output_dir)
            elif system == "Darwin":  # macOS
                subprocess.Popen(["open", output_dir])
            else:  # Linux
                subprocess.Popen(["xdg-open", output_dir])
            self.log(f"출력 디렉토리 열기: {output_dir}")
        except Exception as e:
            self.log(f"디렉토리 열기 실패: {str(e)}")
    
    def log(self, message):
        """로그 메시지 출력"""
        self.log_text.config(state=tk.NORMAL)
        self.log_text.insert(tk.END, f"[{datetime.now().strftime('%H:%M:%S')}] {message}\n")
        self.log_text.see(tk.END)
        self.log_text.config(state=tk.DISABLED)
        self.root.update()
    
    def execute_operation(self):
        """선택된 작업 실행"""
        operation = self.operation_type.get()
        prompt = self.prompt_text.get("1.0", tk.END).strip()
        
        # 입력 검증
        if operation in ["Image to Image", "Image to Video"]:
            input_file = self.input_file_path.get()
            if not input_file or not os.path.exists(input_file):
                messagebox.showerror("Error", "유효한 입력 파일을 선택하세요.")
                return
        
        # 프롬프트 입력 검증
        if not prompt:
            messagebox.showwarning("Warning", "프롬프트를 입력하세요.")
            return
        
        # 출력 디렉토리 생성
        output_dir = self.output_directory.get()
        os.makedirs(output_dir, exist_ok=True)
        
        # 버튼 비활성화
        self.execute_button.config(state=tk.DISABLED)
        self.progress_label.config(text="처리 중...")
        self.log("작업 시작: " + operation)
        
        # 별도 스레드에서 실행
        thread = Thread(target=self.run_operation, args=(operation, prompt))
        thread.daemon = True
        thread.start()
    
    def run_operation(self, operation, prompt):
        """실제 작업 실행 (별도 스레드)"""
        try:
            if operation == "Text to Image":
                self.text_to_image(prompt)
            elif operation == "Image to Image":
                self.image_to_image(prompt)
            elif operation == "Text to Video":
                self.text_to_video(prompt)
            elif operation == "Image to Video":
                self.image_to_video(prompt)
            elif operation == "Text to Speech":
                self.text_to_speech(prompt)
            
            self.progress_label.config(text="완료!")
            self.log("작업 완료")
            
            # 출력 디렉토리 열기
            self.open_output_directory()
            
            messagebox.showinfo("완료", "작업이 성공적으로 완료되었습니다.")
        except Exception as e:
            self.log(f"오류 발생: {str(e)}")
            self.progress_label.config(text="오류 발생")
            messagebox.showerror("Error", f"오류가 발생했습니다:\n{str(e)}")
        finally:
            self.execute_button.config(state=tk.NORMAL)
            self.progress_label.config(text="")
    
    def text_to_image(self, prompt):
        """Text to Image 작업"""
        aspect_ratio = self.aspect_ratio.get()
        self.log(f"이미지 생성 중... (비율: {aspect_ratio})")
        
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
        
        for chunk in self.genai_client.models.generate_content_stream(
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
                
                output_path = os.path.join(
                    self.output_directory.get(), 
                    f"output_{timestamp}{file_extension}"
                )
                with open(output_path, "wb") as f:
                    f.write(data_buffer)
                self.log(f"저장됨: {output_path}")
            else:
                if hasattr(chunk, 'text'):
                    self.log(chunk.text)
    
    def image_to_image(self, prompt):
        """Image to Image 작업"""
        input_path = self.input_file_path.get()
        self.log(f"이미지 편집 중: {input_path}")
        
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
        
        img_to_edit = Image.open(input_path)
        response = self.genai_client.models.generate_content(
            model='gemini-2.5-flash-image-preview',
            contents=[img_to_edit, prompt],
            config=types.GenerateContentConfig(
                safety_settings=safety_settings
            )
        )
        
        # 응답 검증
        if not response.candidates:
            error_msg = f"이미지 생성 실패: 응답에 후보가 없습니다. Response: {response}"
            self.log(error_msg)
            raise Exception(error_msg)
        
        if hasattr(response.candidates[0], 'finish_reason') and response.candidates[0].finish_reason:
            finish_reason = response.candidates[0].finish_reason
            if finish_reason not in ['STOP', 'MAX_TOKENS']:
                error_msg = f"이미지 생성 실패: {finish_reason}"
                self.log(error_msg)
                raise Exception(error_msg)
        
        if response.candidates and response.candidates[0].content.parts:
            for part in response.candidates[0].content.parts:
                if part.inline_data:
                    image_data = BytesIO(part.inline_data.data)
                    img = Image.open(image_data)
                    
                    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
                    output_path = os.path.join(
                        self.output_directory.get(),
                        f"output_{timestamp}.png"
                    )
                    img.save(output_path)
                    self.log(f"저장됨: {output_path}")
                    return
        
        error_msg = f"이미지 생성 실패: 응답에 이미지 데이터가 없습니다. Response: {response}"
        self.log(error_msg)
        raise Exception(error_msg)
    
    def text_to_video(self, prompt):
        """Text to Video 작업"""
        resolution = self.video_resolution.get()
        aspect_ratio = self.video_aspect_ratio.get()
        self.log(f"비디오 생성 중... (해상도: {resolution}, 비율: {aspect_ratio}, 시간이 다소 걸릴 수 있습니다)")
        
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
        
        model = "veo-3.1-generate-preview"
        operation = self.genai_client.models.generate_videos(
            model=model,
            prompt=prompt,
            config=types.GenerateVideosConfig(
                resolution=resolution,
                aspect_ratio=aspect_ratio,
                safety_settings=safety_settings
            )
        )
        
        # 작업 완료 대기
        while not operation.done:
            self.log("비디오 생성 대기 중...")
            time.sleep(10)
            operation = self.genai_client.operations.get(operation)
        
        # 작업 결과 확인
        if hasattr(operation, 'error') and operation.error:
            error_msg = f"비디오 생성 실패: {operation.error}"
            self.log(error_msg)
            raise Exception(error_msg)
        
        if not operation.response or not operation.response.generated_videos:
            error_msg = f"비디오 생성 실패: 응답에 비디오가 없습니다. Response: {operation.response}"
            self.log(error_msg)
            raise Exception(error_msg)
        
        if len(operation.response.generated_videos) == 0:
            error_msg = "비디오 생성 실패: 생성된 비디오가 없습니다."
            self.log(error_msg)
            raise Exception(error_msg)
        
        # 비디오 다운로드
        generated_video = operation.response.generated_videos[0]
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        output_path = os.path.join(
            self.output_directory.get(),
            f"output_{timestamp}.mp4"
        )
        
        self.genai_client.files.download(file=generated_video.video)
        generated_video.video.save(output_path)
        self.log(f"저장됨: {output_path}")
    
    def image_to_video(self, prompt):
        """Image to Video 작업"""
        input_path = self.input_file_path.get()
        resolution = self.video_resolution.get()
        aspect_ratio = self.video_aspect_ratio.get()
        self.log(f"비디오 생성 중: {input_path} (해상도: {resolution}, 비율: {aspect_ratio}, 시간이 다소 걸릴 수 있습니다)")
        
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
        
        # PIL Image 로드 및 바이트로 변환
        pil_image = Image.open(input_path)
        img_byte_arr = BytesIO()
        
        # MIME 타입 추론
        mime_type = mimetypes.guess_type(input_path)[0]
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
        
        operation = self.genai_client.models.generate_videos(
            model=model,
            prompt=prompt,
            image=safe_image,
            config=types.GenerateVideosConfig(
                resolution=resolution,
                aspect_ratio=aspect_ratio,
                safety_settings=safety_settings
            )
        )
        
        # 작업 완료 대기
        while not operation.done:
            self.log("비디오 생성 대기 중...")
            time.sleep(10)
            operation = self.genai_client.operations.get(operation)
        
        # 작업 결과 확인
        if hasattr(operation, 'error') and operation.error:
            error_msg = f"비디오 생성 실패: {operation.error}"
            self.log(error_msg)
            raise Exception(error_msg)
        
        if not operation.response or not operation.response.generated_videos:
            error_msg = f"비디오 생성 실패: 응답에 비디오가 없습니다. Response: {operation.response}"
            self.log(error_msg)
            raise Exception(error_msg)
        
        if len(operation.response.generated_videos) == 0:
            error_msg = "비디오 생성 실패: 생성된 비디오가 없습니다."
            self.log(error_msg)
            raise Exception(error_msg)
        
        # 비디오 다운로드
        video = operation.response.generated_videos[0]
        self.genai_client.files.download(file=video.video)
        
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        output_path = os.path.join(
            self.output_directory.get(),
            f"output_{timestamp}.mp4"
        )
        
        video.video.save(output_path)
        self.log(f"저장됨: {output_path}")
    
    def text_to_speech(self, text):
        """Text to Speech 작업"""
        # voice_name에서 실제 이름만 추출 ("Zephyr -- Bright" -> "Zephyr")
        voice_display = self.voice_name.get()
        voice_name_only = voice_display.split(" -- ")[0]
        
        self.log(f"음성 생성 중... (음성: {voice_display})")
        
        model = "gemini-2.5-pro-preview-tts"
        contents = [
            types.Content(
                role="user",
                parts=[types.Part.from_text(text=text)],
            ),
        ]
        generate_content_config = types.GenerateContentConfig(
            temperature=1,
            response_modalities=["audio"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=voice_name_only
                    )
                )
            ),
        )
        
        for chunk in self.genai_client.models.generate_content_stream(
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
                    data_buffer = self.convert_to_wav(inline_data.data, inline_data.mime_type)
                
                output_path = os.path.join(
                    self.output_directory.get(),
                    f"{file_name}{file_extension}"
                )
                with open(output_path, "wb") as f:
                    f.write(data_buffer)
                self.log(f"저장됨: {output_path}")
            else:
                if hasattr(chunk, 'text'):
                    self.log(chunk.text)
    
    def convert_to_wav(self, audio_data: bytes, mime_type: str) -> bytes:
        """오디오 데이터를 WAV 포맷으로 변환"""
        parameters = self.parse_audio_mime_type(mime_type)
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
    
    def parse_audio_mime_type(self, mime_type: str) -> dict:
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
    
    def save_new_prompt(self):
        """새 프롬프트 저장"""
        prompt = self.prompt_text.get("1.0", tk.END).strip()
        if not prompt:
            messagebox.showwarning("Warning", "저장할 프롬프트를 입력하세요.")
            return
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("INSERT INTO prompt (content) VALUES (?)", (prompt,))
        conn.commit()
        conn.close()
        
        messagebox.showinfo("저장 완료", "프롬프트가 저장되었습니다.")
        self.log("프롬프트 저장됨")
    
    def update_prompt(self):
        """현재 프롬프트 수정"""
        if self.current_prompt_id is None:
            messagebox.showwarning("Warning", "목록에서 프롬프트를 선택한 후 수정할 수 있습니다.")
            return
        
        prompt = self.prompt_text.get("1.0", tk.END).strip()
        if not prompt:
            messagebox.showwarning("Warning", "수정할 프롬프트를 입력하세요.")
            return
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE prompt SET content = ?, created_at = CURRENT_TIMESTAMP WHERE id = ?",
            (prompt, self.current_prompt_id)
        )
        conn.commit()
        conn.close()
        
        messagebox.showinfo("수정 완료", "프롬프트가 수정되었습니다.")
        self.log(f"프롬프트 ID {self.current_prompt_id} 수정됨")
    
    def delete_prompt(self):
        """현재 프롬프트 삭제"""
        if self.current_prompt_id is None:
            messagebox.showwarning("Warning", "목록에서 프롬프트를 선택한 후 삭제할 수 있습니다.")
            return
        
        result = messagebox.askyesno("삭제 확인", "선택한 프롬프트를 삭제하시겠습니까?")
        if not result:
            return
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM prompt WHERE id = ?", (self.current_prompt_id,))
        conn.commit()
        conn.close()
        
        self.prompt_text.delete("1.0", tk.END)
        self.current_prompt_id = None
        
        messagebox.showinfo("삭제 완료", "프롬프트가 삭제되었습니다.")
        self.log("프롬프트 삭제됨")
    
    def show_prompt_list(self):
        """프롬프트 목록 표시"""
        list_window = tk.Toplevel(self.root)
        list_window.title("프롬프트 목록")
        list_window.geometry("700x500")
        
        # 프레임 구성
        main_frame = ttk.Frame(list_window, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # 리스트박스와 스크롤바
        list_frame = ttk.Frame(main_frame)
        list_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 10))
        
        scrollbar = ttk.Scrollbar(list_frame)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        listbox = tk.Listbox(
            list_frame, 
            yscrollcommand=scrollbar.set,
            font=("", 10),
            activestyle='dotbox'
        )
        listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.config(command=listbox.yview)
        
        # 프롬프트 미리보기
        preview_label = ttk.Label(main_frame, text="프롬프트 미리보기:", font=("", 10, "bold"))
        preview_label.pack(anchor=tk.W, pady=(0, 5))
        
        preview_text = scrolledtext.ScrolledText(main_frame, height=8, wrap=tk.WORD, state=tk.DISABLED)
        preview_text.pack(fill=tk.BOTH, expand=True, pady=(0, 10))
        
        # 데이터베이스에서 프롬프트 가져오기
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT id, content, created_at FROM prompt ORDER BY created_at DESC")
        prompts = cursor.fetchall()
        conn.close()
        
        # 프롬프트 ID 매핑
        prompt_map = {}
        
        for prompt_id, content, created_at in prompts:
            # 프롬프트 앞부분만 표시 (최대 80자)
            preview = content[:80].replace('\n', ' ')
            if len(content) > 80:
                preview += "..."
            
            timestamp = datetime.strptime(created_at, "%Y-%m-%d %H:%M:%S").strftime("%Y-%m-%d %H:%M")
            display_text = f"[{timestamp}] {preview}"
            
            listbox.insert(tk.END, display_text)
            prompt_map[listbox.size() - 1] = (prompt_id, content)
        
        def on_select(event):
            """리스트 항목 선택 시 미리보기 표시"""
            selection = listbox.curselection()
            if selection:
                index = selection[0]
                _, content = prompt_map[index]
                preview_text.config(state=tk.NORMAL)
                preview_text.delete("1.0", tk.END)
                preview_text.insert("1.0", content)
                preview_text.config(state=tk.DISABLED)
        
        def on_load():
            """선택한 프롬프트 불러오기"""
            selection = listbox.curselection()
            if not selection:
                messagebox.showwarning("Warning", "프롬프트를 선택하세요.")
                return
            
            index = selection[0]
            prompt_id, content = prompt_map[index]
            
            self.prompt_text.delete("1.0", tk.END)
            self.prompt_text.insert("1.0", content)
            self.current_prompt_id = prompt_id
            
            list_window.destroy()
            self.log(f"프롬프트 ID {prompt_id} 불러옴")
        
        def on_delete():
            """선택한 프롬프트 삭제"""
            selection = listbox.curselection()
            if not selection:
                messagebox.showwarning("Warning", "삭제할 프롬프트를 선택하세요.")
                return
            
            index = selection[0]
            prompt_id, _ = prompt_map[index]
            
            result = messagebox.askyesno("삭제 확인", "선택한 프롬프트를 삭제하시겠습니까?")
            if not result:
                return
            
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("DELETE FROM prompt WHERE id = ?", (prompt_id,))
            conn.commit()
            conn.close()
            
            listbox.delete(index)
            del prompt_map[index]
            # 인덱스 재정렬
            new_map = {}
            for i, key in enumerate(sorted(prompt_map.keys())):
                if key > index:
                    new_map[i] = prompt_map[key]
                elif key < index:
                    new_map[key] = prompt_map[key]
            prompt_map.clear()
            prompt_map.update(new_map)
            
            preview_text.config(state=tk.NORMAL)
            preview_text.delete("1.0", tk.END)
            preview_text.config(state=tk.DISABLED)
            
            self.log("프롬프트 삭제됨")
        
        listbox.bind('<<ListboxSelect>>', on_select)
        listbox.bind('<Double-Button-1>', lambda e: on_load())
        
        # 버튼 프레임
        button_frame = ttk.Frame(main_frame)
        button_frame.pack(fill=tk.X)
        
        ttk.Button(button_frame, text="불러오기", command=on_load).pack(side=tk.LEFT, padx=5)
        ttk.Button(button_frame, text="삭제", command=on_delete).pack(side=tk.LEFT, padx=5)
        ttk.Button(button_frame, text="닫기", command=list_window.destroy).pack(side=tk.RIGHT, padx=5)
        
        if prompts:
            listbox.selection_set(0)
            on_select(None)
    
    def show_about(self):
        """정보 다이얼로그"""
        messagebox.showinfo(
            "정보",
            "Google Gemini API Tools\n\n"
            "Version 1.0\n\n"
            "Google Gemini API를 사용하여\n"
            "이미지, 비디오, 음성을 생성하는 도구입니다."
        )


def main():
    # 드래그앤드랍이 가능한 경우 TkinterDnD.Tk 사용
    if DRAG_DROP_AVAILABLE:
        root = TkinterDnD.Tk()
    else:
        root = tk.Tk()
    
    GoogleAPIToolsGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()

