// DOM 요소
const operationType = document.getElementById('operationType');
const fileInputCard = document.getElementById('fileInputCard');
const fileInputCardTitle = document.getElementById('fileInputCardTitle');
const fileInput = document.getElementById('fileInput');
const dropZone = document.getElementById('dropZone');
const dropZoneText = document.getElementById('dropZoneText');
const fileInputHint = document.getElementById('fileInputHint');
const selectedFileName = document.getElementById('selectedFileName');
const imageRatioGroup = document.getElementById('imageRatioGroup');
const videoSettingsGroup = document.getElementById('videoSettingsGroup');
const voiceSettingsGroup = document.getElementById('voiceSettingsGroup');
const aspectRatio = document.getElementById('aspectRatio');
const videoResolution = document.getElementById('videoResolution');
const videoAspectRatio = document.getElementById('videoAspectRatio');
const voiceName = document.getElementById('voiceName');
const promptText = document.getElementById('promptText');
const executeBtn = document.getElementById('executeBtn');
const progressAlert = document.getElementById('progressAlert');
const progressMessage = document.getElementById('progressMessage');
const resultCard = document.getElementById('resultCard');
const resultContent = document.getElementById('resultContent');
const logContainer = document.getElementById('logContainer');
const savePromptBtn = document.getElementById('savePromptBtn');
const loadPromptBtn = document.getElementById('loadPromptBtn');
const promptModal = new bootstrap.Modal(document.getElementById('promptModal'));
const promptList = document.getElementById('promptList');
const imagePreviewContainer = document.getElementById('imagePreviewContainer');

let selectedFiles = [];
let currentPromptId = null;
let MAX_FILES = 3;

// 로그 추가 함수
function log(message, isLlmResponse = false) {
    const now = new Date();
    const timestamp = `${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}:${String(now.getSeconds()).padStart(2, '0')}`;
    const logEntry = document.createElement('div');
    logEntry.className = isLlmResponse ? 'log-entry llm-response' : 'log-entry';
    logEntry.style.whiteSpace = 'pre-wrap'; // 줄바꿈 유지
    logEntry.textContent = `[${timestamp}] ${message}`;
    logContainer.appendChild(logEntry);
    logContainer.scrollTop = logContainer.scrollHeight;
}

// 에러 로그 추가 함수 (빨간색)
function logError(message) {
    const now = new Date();
    const timestamp = `${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}:${String(now.getSeconds()).padStart(2, '0')}`;
    const logEntry = document.createElement('div');
    logEntry.className = 'log-entry';
    logEntry.style.whiteSpace = 'pre-wrap'; // 줄바꿈 유지
    logEntry.style.color = '#ff4444'; // 빨간색
    logEntry.style.fontWeight = 'bold';
    logEntry.textContent = `[${timestamp}] ${message}`;
    logContainer.appendChild(logEntry);
    logContainer.scrollTop = logContainer.scrollHeight;
}

// 이미지/비디오 미리보기 업데이트 함수
function updateImagePreview() {
    imagePreviewContainer.innerHTML = '';
    
    if (selectedFiles.length === 0) {
        selectedFileName.textContent = '';
        return;
    }
    
    if (selectedFiles.length === 1) {
        selectedFileName.textContent = `선택된 파일: ${selectedFiles[0].name}`;
    } else {
        selectedFileName.textContent = `선택된 파일: ${selectedFiles.length}개`;
    }
    
    const operation = operationType.value;
    
    selectedFiles.forEach((file, index) => {
        const reader = new FileReader();
        reader.onload = (e) => {
            const previewItem = document.createElement('div');
            previewItem.className = 'image-preview-item';
            
            // 비디오 파일인 경우
            if (operation === 'video-to-video' && file.type.startsWith('video/')) {
                previewItem.innerHTML = `
                    <div class="image-number">${index + 1}</div>
                    <video src="${e.target.result}" style="width: 100%; height: 100%; object-fit: cover;"></video>
                    <button class="remove-image" data-index="${index}" title="삭제">×</button>
                `;
            } else {
                // 이미지 파일인 경우
                previewItem.innerHTML = `
                    <div class="image-number">${index + 1}</div>
                    <img src="${e.target.result}" alt="Preview ${index + 1}">
                    <button class="remove-image" data-index="${index}" title="삭제">×</button>
                `;
            }
            
            const removeBtn = previewItem.querySelector('.remove-image');
            removeBtn.addEventListener('click', () => removeFile(index));
            
            imagePreviewContainer.appendChild(previewItem);
        };
        reader.readAsDataURL(file);
    });
}

// 파일 제거 함수
function removeFile(index) {
    selectedFiles.splice(index, 1);
    updateImagePreview();
    log(`파일 제거됨 (남은 파일: ${selectedFiles.length}개)`);
}

// 파일 추가 함수
function addFiles(files) {
    const newFiles = Array.from(files);
    
    if (selectedFiles.length + newFiles.length > MAX_FILES) {
        alert(`최대 ${MAX_FILES}개의 파일만 선택할 수 있습니다.`);
        const allowedCount = MAX_FILES - selectedFiles.length;
        selectedFiles = selectedFiles.concat(newFiles.slice(0, allowedCount));
    } else {
        selectedFiles = selectedFiles.concat(newFiles);
    }
    
    updateImagePreview();
    log(`파일 추가됨: ${newFiles.length}개 (전체: ${selectedFiles.length}개)`);
}

// 작업 유형에 따른 UI 업데이트 함수
function updateUIForOperation() {
    const operation = operationType.value;
    
    // 파일 입력 초기화
    selectedFiles = [];
    selectedFileName.textContent = '';
    imagePreviewContainer.innerHTML = '';
    fileInputHint.textContent = '';
    
    // 파일 입력 표시 여부
    if (operation === 'image-to-image' || operation === 'image-to-video') {
        fileInputCard.style.display = 'block';
        fileInputCardTitle.innerHTML = '<i class="bi bi-file-earmark-image"></i> 입력 파일 (최대 3장)';
        dropZoneText.textContent = '여기에 파일을 드래그앤드롭하거나';
        fileInput.accept = 'image/*';
        fileInput.multiple = true;
        MAX_FILES = 3;
    } else if (operation === 'video-to-video') {
        fileInputCard.style.display = 'block';
        fileInputCardTitle.innerHTML = '<i class="bi bi-file-earmark-play"></i> 입력 비디오 파일 (최대 1개)';
        dropZoneText.textContent = '여기에 비디오 파일을 드래그앤드롭하거나';
        fileInputHint.textContent = 'Text To Video/Image to Video를 통해 생성한 Video 파일을 업로드 해주세요';
        fileInput.accept = 'video/*';
        fileInput.multiple = false;
        MAX_FILES = 1;
    } else {
        fileInputCard.style.display = 'none';
    }
    
    // 설정 표시 여부
    imageRatioGroup.style.display = operation === 'text-to-image' ? 'block' : 'none';
    videoSettingsGroup.style.display = (operation === 'text-to-video' || operation === 'image-to-video' || operation === 'video-to-video') ? 'block' : 'none';
    voiceSettingsGroup.style.display = operation === 'text-to-speech' ? 'block' : 'none';
}

// 작업 유형 변경 시
operationType.addEventListener('change', updateUIForOperation);

// 파일 선택
fileInput.addEventListener('change', (e) => {
    if (e.target.files.length > 0) {
        addFiles(e.target.files);
    }
});

// 드래그앤드롭 이벤트
['dragenter', 'dragover', 'dragleave', 'drop'].forEach(eventName => {
    dropZone.addEventListener(eventName, preventDefaults, false);
});

function preventDefaults(e) {
    e.preventDefault();
    e.stopPropagation();
}

['dragenter', 'dragover'].forEach(eventName => {
    dropZone.addEventListener(eventName, () => {
        dropZone.classList.add('drag-over');
    }, false);
});

['dragleave', 'drop'].forEach(eventName => {
    dropZone.addEventListener(eventName, () => {
        dropZone.classList.remove('drag-over');
    }, false);
});

dropZone.addEventListener('drop', (e) => {
    const dt = e.dataTransfer;
    const files = dt.files;
    
    if (files.length > 0) {
        addFiles(files);
    }
});

// 실행 버튼
executeBtn.addEventListener('click', async () => {
    const operation = operationType.value;
    const prompt = promptText.value.trim();
    
    // 유효성 검사
    if (!prompt) {
        alert('프롬프트를 입력하세요.');
        return;
    }
    
    if ((operation === 'image-to-image' || operation === 'image-to-video' || operation === 'video-to-video') && selectedFiles.length === 0) {
        alert('입력 파일을 선택하세요.');
        return;
    }
    
    // UI 상태 변경
    executeBtn.disabled = true;
    progressAlert.classList.remove('d-none');
    resultCard.classList.add('d-none');
    
    log(`작업 시작: ${operation}`);
    progressMessage.textContent = '처리 중...';
    
    try {
        let result;
        
        switch (operation) {
            case 'text-to-image':
                result = await executeTextToImage(prompt, aspectRatio.value);
                break;
            case 'image-to-image':
                result = await executeImageToImage(prompt, selectedFiles);
                break;
            case 'text-to-video':
                result = await executeTextToVideo(prompt, videoResolution.value, videoAspectRatio.value);
                break;
            case 'image-to-video':
                result = await executeImageToVideo(prompt, selectedFiles, videoResolution.value, videoAspectRatio.value);
                break;
            case 'video-to-video':
                result = await executeVideoToVideo(prompt, selectedFiles, videoResolution.value, videoAspectRatio.value);
                break;
            case 'text-to-speech':
                result = await executeTextToSpeech(prompt, voiceName.value);
                break;
        }
        
        if (result && result.status === 'success') {
            // LLM 응답이 있으면 로그에 표시 (파란색으로)
            if (result.llm_response) {
                log(`LLM 응답: ${result.llm_response}`, true);
            }
            log('작업 완료');
            displayResult(result, operation);
        } else {
            throw new Error('작업 실패');
        }
        
    } catch (error) {
        // 로그에는 전체 에러 내용 출력 (빨간색으로 표시)
        if (error.details) {
            logError(`오류 발생:\n${error.details}`);
        } else {
            logError(`오류 발생: ${error.message}`);
        }
        // 팝업은 간단하게
        alert('작업 실패: 자세한 내용은 로그를 확인하세요.');
    } finally {
        executeBtn.disabled = false;
        progressAlert.classList.add('d-none');
    }
});

// API 호출 함수들
async function executeTextToImage(prompt, aspectRatio) {
    const formData = new FormData();
    formData.append('prompt', prompt);
    formData.append('aspect_ratio', aspectRatio);
    
    const response = await fetch('/api/text-to-image', {
        method: 'POST',
        body: formData
    });
    
    const result = await response.json();
    
    if (!response.ok) {
        const error = new Error(result.detail || '작업 실패');
        error.details = result.detail;
        throw error;
    }
    
    return result;
}

async function executeImageToImage(prompt, files) {
    const formData = new FormData();
    formData.append('prompt', prompt);
    
    // 멀티 파일 업로드
    files.forEach((file, index) => {
        formData.append('files', file);
    });
    
    const response = await fetch('/api/image-to-image', {
        method: 'POST',
        body: formData
    });
    
    const result = await response.json();
    
    if (!response.ok) {
        const error = new Error(result.detail || '작업 실패');
        error.details = result.detail;
        throw error;
    }
    
    return result;
}

async function executeTextToVideo(prompt, resolution, aspectRatio) {
    log('비디오 생성 중... (시간이 다소 걸릴 수 있습니다)');
    
    const formData = new FormData();
    formData.append('prompt', prompt);
    formData.append('resolution', resolution);
    formData.append('aspect_ratio', aspectRatio);
    
    const response = await fetch('/api/text-to-video', {
        method: 'POST',
        body: formData
    });
    
    const result = await response.json();
    
    if (!response.ok) {
        const error = new Error(result.detail || '작업 실패');
        error.details = result.detail;
        throw error;
    }
    
    return result;
}

async function executeImageToVideo(prompt, files, resolution, aspectRatio) {
    log('비디오 생성 중... (시간이 다소 걸릴 수 있습니다)');
    
    const formData = new FormData();
    formData.append('prompt', prompt);
    
    // 멀티 파일 업로드
    files.forEach((file, index) => {
        formData.append('files', file);
    });
    
    formData.append('resolution', resolution);
    formData.append('aspect_ratio', aspectRatio);
    
    const response = await fetch('/api/image-to-video', {
        method: 'POST',
        body: formData
    });
    
    const result = await response.json();
    
    if (!response.ok) {
        const error = new Error(result.detail || '작업 실패');
        error.details = result.detail;
        throw error;
    }
    
    return result;
}

async function executeVideoToVideo(prompt, files, resolution, aspectRatio) {
    log('비디오 확장 중... (시간이 다소 걸릴 수 있습니다)');
    
    const formData = new FormData();
    formData.append('prompt', prompt);
    formData.append('file', files[0]);
    formData.append('resolution', resolution);
    formData.append('aspect_ratio', aspectRatio);
    
    const response = await fetch('/api/video-to-video', {
        method: 'POST',
        body: formData
    });
    
    const result = await response.json();
    
    if (!response.ok) {
        const error = new Error(result.detail || '작업 실패');
        error.details = result.detail;
        throw error;
    }
    
    return result;
}

async function executeTextToSpeech(prompt, voiceName) {
    const formData = new FormData();
    formData.append('prompt', prompt);
    formData.append('voice_name', voiceName);
    
    const response = await fetch('/api/text-to-speech', {
        method: 'POST',
        body: formData
    });
    
    const result = await response.json();
    
    if (!response.ok) {
        const error = new Error(result.detail || '작업 실패');
        error.details = result.detail;
        throw error;
    }
    
    return result;
}

// 결과 표시
function displayResult(result, operation) {
    resultCard.classList.remove('d-none');
    
    const fileType = operation.includes('video') ? 'video' : 
                     operation.includes('speech') ? 'audio' : 'image';
    
    let content = '';
    
    if (fileType === 'image') {
        content = `
            <img src="${result.output_file}" class="img-fluid mb-3" alt="Generated Image">
            <div>
                <a href="${result.output_file}" download class="btn btn-primary">
                    <i class="bi bi-download"></i> 다운로드
                </a>
            </div>
        `;
    } else if (fileType === 'video') {
        content = `
            <video controls class="w-100 mb-3">
                <source src="${result.output_file}" type="video/mp4">
            </video>
            <div>
                <a href="${result.output_file}" download class="btn btn-primary">
                    <i class="bi bi-download"></i> 다운로드
                </a>
            </div>
        `;
    } else if (fileType === 'audio') {
        content = `
            <audio controls class="w-100 mb-3">
                <source src="${result.output_file}">
            </audio>
            <div>
                <a href="${result.output_file}" download class="btn btn-primary">
                    <i class="bi bi-download"></i> 다운로드
                </a>
            </div>
        `;
    }
    
    resultContent.innerHTML = content;
}

// 프롬프트 저장
savePromptBtn.addEventListener('click', async () => {
    const prompt = promptText.value.trim();
    
    if (!prompt) {
        alert('저장할 프롬프트를 입력하세요.');
        return;
    }
    
    try {
        const response = await fetch('/api/prompts', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ content: prompt })
        });
        
        const result = await response.json();
        
        if (result.status === 'success') {
            log('프롬프트 저장됨');
            alert('프롬프트가 저장되었습니다.');
        }
    } catch (error) {
        logError(`프롬프트 저장 오류: ${error.message}`);
        alert('프롬프트 저장 중 오류가 발생했습니다.');
    }
});

// 프롬프트 목록 불러오기
loadPromptBtn.addEventListener('click', async () => {
    try {
        const response = await fetch('/api/prompts');
        const result = await response.json();
        
        promptList.innerHTML = '';
        
        if (result.prompts.length === 0) {
            promptList.innerHTML = '<div class="text-muted text-center p-3">저장된 프롬프트가 없습니다.</div>';
        } else {
            result.prompts.forEach(prompt => {
                const preview = prompt.content.substring(0, 100);
                const displayText = prompt.content.length > 100 ? preview + '...' : preview;
                
                const item = document.createElement('button');
                item.className = 'list-group-item list-group-item-action';
                item.innerHTML = `
                    <div class="d-flex justify-content-between align-items-start">
                        <div class="flex-grow-1">
                            <div class="fw-bold">${prompt.created_at}</div>
                            <div class="text-muted small mt-1">${displayText}</div>
                        </div>
                        <button class="btn btn-sm btn-danger ms-2 delete-prompt" data-id="${prompt.id}">
                            <i class="bi bi-trash"></i>
                        </button>
                    </div>
                `;
                
                item.addEventListener('click', (e) => {
                    if (!e.target.closest('.delete-prompt')) {
                        promptText.value = prompt.content;
                        currentPromptId = prompt.id;
                        promptModal.hide();
                        log(`프롬프트 ID ${prompt.id} 불러옴`);
                    }
                });
                
                const deleteBtn = item.querySelector('.delete-prompt');
                deleteBtn.addEventListener('click', async (e) => {
                    e.stopPropagation();
                    
                    if (!confirm('이 프롬프트를 삭제하시겠습니까?')) {
                        return;
                    }
                    
                    try {
                        const response = await fetch(`/api/prompts/${prompt.id}`, {
                            method: 'DELETE'
                        });
                        
                        const result = await response.json();
                        
                        if (result.status === 'success') {
                            item.remove();
                            log('프롬프트 삭제됨');
                        }
                    } catch (error) {
                        alert('프롬프트 삭제 중 오류가 발생했습니다.');
                    }
                });
                
                promptList.appendChild(item);
            });
        }
        
        promptModal.show();
    } catch (error) {
        logError(`프롬프트 목록 불러오기 오류: ${error.message}`);
        alert('프롬프트 목록을 불러오는 중 오류가 발생했습니다.');
    }
});

// 초기화
updateUIForOperation();
log('웹 애플리케이션 준비 완료');

