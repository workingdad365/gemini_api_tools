// DOM 요소
const operationType = document.getElementById('operationType');
const fileInputCard = document.getElementById('fileInputCard');
const fileInputCardTitle = document.getElementById('fileInputCardTitle');
const fileInput = document.getElementById('fileInput');
const dropZone = document.getElementById('dropZone');
const dropZoneText = document.getElementById('dropZoneText');
const fileInputHint = document.getElementById('fileInputHint');
const selectedFileName = document.getElementById('selectedFileName');
const imageModelGroup = document.getElementById('imageModelGroup');
const imageModel = document.getElementById('imageModel');
const imageResolutionGroup = document.getElementById('imageResolutionGroup');
const imageResolution = document.getElementById('imageResolution');
const imageRatioGroup = document.getElementById('imageRatioGroup');
const videoSettingsGroup = document.getElementById('videoSettingsGroup');
const voiceSettingsGroup = document.getElementById('voiceSettingsGroup');
const aspectRatio = document.getElementById('aspectRatio');
const videoResolution = document.getElementById('videoResolution');
const videoAspectRatio = document.getElementById('videoAspectRatio');
const voiceName = document.getElementById('voiceName');
const promptText = document.getElementById('promptText');
const executeBtn = document.getElementById('executeBtn');
const executeBtnLabel = document.getElementById('executeBtnLabel');
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
const newSessionBtn = document.getElementById('newSessionBtn');
const sessionAlert = document.getElementById('sessionAlert');
const sessionMessage = document.getElementById('sessionMessage');
const clearSessionBtn = document.getElementById('clearSessionBtn');
const toggleSettingsBtn = document.getElementById('toggleSettingsBtn');
const settingsBody = document.getElementById('settingsBody');

let selectedFiles = [];
let currentPromptId = null;
let MAX_FILES = 3;
let lastGeneratedVideoUUID = null; // 마지막 생성된 비디오 UUID 저장
let lastVideoResolution = null; // 마지막 생성된 비디오 해상도 저장
let lastImageSessionId = null; // 마지막 이미지 생성 세션 ID (Multi-turn용)
let isSettingsVisible = false;
let lastOperationType = null;

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

// 이미지 세션 초기화 함수
function clearImageSession() {
    lastImageSessionId = null;
    updateSessionUI();
    log('이미지 세션 초기화됨');
}

// 세션 UI 업데이트 함수
function updateSessionUI() {
    const operation = operationType.value;
    const isImageOperation = (operation === 'text-to-image' || operation === 'image-to-image');
    
    if (isImageOperation && lastImageSessionId) {
        // 세션이 있으면 새 실행 버튼과 세션 알림 표시
        newSessionBtn.classList.remove('d-none');
        sessionAlert.classList.remove('d-none');
        sessionMessage.textContent = `편집 모드 활성화됨 (${operation === 'text-to-image' ? 'Text to Image' : 'Image to Image'})`;
        executeBtnLabel.textContent = '편집하기';
    } else {
        // 세션이 없으면 숨김
        newSessionBtn.classList.add('d-none');
        sessionAlert.classList.add('d-none');
        executeBtnLabel.textContent = '실행하기';
    }
}

// 세션 초기화 버튼 이벤트
clearSessionBtn.addEventListener('click', clearImageSession);

// 설정 표시 토글
function toggleSettingsVisibility() {
    if (!settingsBody || !toggleSettingsBtn) {
        return;
    }
    isSettingsVisible = !isSettingsVisible;
    if (isSettingsVisible) {
        settingsBody.classList.remove('d-none');
        toggleSettingsBtn.innerHTML = '<i class="bi bi-chevron-up me-1"></i> 숨기기';
    } else {
        settingsBody.classList.add('d-none');
        toggleSettingsBtn.innerHTML = '<i class="bi bi-chevron-down me-1"></i> 보이기';
    }
}

if (toggleSettingsBtn) {
    toggleSettingsBtn.addEventListener('click', (event) => {
        event.preventDefault();
        toggleSettingsVisibility();
    });
    toggleSettingsBtn.addEventListener('touchstart', (event) => {
        event.preventDefault();
        toggleSettingsVisibility();
    }, { passive: false });
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

// MAX_FILES 업데이트 함수
function updateMaxFiles() {
    const operation = operationType.value;
    const selectedModel = imageModel.value;
    
    if (operation === 'image-to-image') {
        if (selectedModel === 'gemini-3-pro-image-preview') {
            MAX_FILES = 14;
        } else {
            MAX_FILES = 3;
        }
        fileInputCardTitle.innerHTML = `<i class="bi bi-file-earmark-image"></i> 입력 파일 (최대 ${MAX_FILES}장)`;
        log(`최대 파일 수 변경: ${MAX_FILES}장`);
    } else if (operation === 'image-to-video') {
        MAX_FILES = 3;
    }
}

// 모델에 따른 해상도 옵션 표시/숨김
function updateResolutionVisibility() {
    const operation = operationType.value;
    const selectedModel = imageModel.value;
    
    // text-to-image 또는 image-to-image이고, Nano-Banana Pro 모델인 경우에만 해상도 표시
    if ((operation === 'text-to-image' || operation === 'image-to-image') && 
        selectedModel === 'gemini-3-pro-image-preview') {
        imageResolutionGroup.style.display = 'block';
    } else {
        imageResolutionGroup.style.display = 'none';
    }
}

// 작업 유형에 따른 UI 업데이트 함수
function updateUIForOperation() {
    const operation = operationType.value;
    
    // 작업 유형 변경 시 이미지 세션 초기화
    if (lastOperationType && lastOperationType !== operation) {
        lastImageSessionId = null;
    }
    lastOperationType = operation;
    
    // 파일 입력 초기화
    selectedFiles = [];
    selectedFileName.textContent = '';
    imagePreviewContainer.innerHTML = '';
    fileInputHint.textContent = '';
    
    // 비디오 확장 관련 초기화
    lastGeneratedVideoUUID = null;
    lastVideoResolution = null;
    
    // 파일 입력 표시 여부
    if (operation === 'image-to-image' || operation === 'image-to-video') {
        fileInputCard.style.display = 'block';
        if (operation === 'image-to-image') {
            updateMaxFiles();
        } else {
            MAX_FILES = 3;
            fileInputCardTitle.innerHTML = '<i class="bi bi-file-earmark-image"></i> 입력 파일 (최대 3장)';
        }
        dropZoneText.textContent = '여기에 파일을 드래그앤드롭하거나';
        fileInput.accept = 'image/*';
        fileInput.multiple = true;
    } else {
        fileInputCard.style.display = 'none';
    }
    
    // 설정 표시 여부
    imageModelGroup.style.display = (operation === 'text-to-image' || operation === 'image-to-image') ? 'block' : 'none';
    imageRatioGroup.style.display = operation === 'text-to-image' ? 'block' : 'none';
    videoSettingsGroup.style.display = (operation === 'text-to-video' || operation === 'image-to-video') ? 'block' : 'none';
    voiceSettingsGroup.style.display = operation === 'text-to-speech' ? 'block' : 'none';
    
    // 해상도 옵션 표시 여부 업데이트
    updateResolutionVisibility();
    
    // 세션 UI 업데이트
    updateSessionUI();
}

// 작업 유형 변경 시
operationType.addEventListener('change', updateUIForOperation);

// 이미지 모델 변경 시
imageModel.addEventListener('change', () => {
    updateMaxFiles();
    updateResolutionVisibility();
    // 파일이 이미 선택되어 있고 MAX_FILES를 초과하는 경우 처리
    if (selectedFiles.length > MAX_FILES) {
        selectedFiles = selectedFiles.slice(0, MAX_FILES);
        updateImagePreview();
        log(`파일 개수가 최대 제한을 초과하여 ${MAX_FILES}개로 조정되었습니다.`);
    }
});

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

// 공통 실행 함수
async function executeOperation(isNew = false) {
    const operation = operationType.value;
    const prompt = promptText.value.trim();
    
    // 유효성 검사
    if (!prompt) {
        alert('프롬프트를 입력하세요.');
        return;
    }
    
    // Image to Image에서 새로 만들기 모드일 때만 파일 필수
    if (operation === 'image-to-image' && isNew && selectedFiles.length === 0) {
        alert('입력 파일을 선택하세요.');
        return;
    }
    
    // Image to Video는 항상 파일 필수
    if (operation === 'image-to-video' && selectedFiles.length === 0) {
        alert('입력 파일을 선택하세요.');
        return;
    }
    
    // UI 상태 변경
    executeBtn.disabled = true;
    newSessionBtn.disabled = true;
    progressAlert.classList.remove('d-none');
    resultCard.classList.add('d-none');
    
    const modeText = isNew ? '실행하기' : (lastImageSessionId ? '편집하기' : '실행하기');
    log(`작업 시작: ${operation} (${modeText})`);
    progressMessage.textContent = '처리 중...';
    
    try {
        let result;
        
        switch (operation) {
            case 'text-to-image':
                result = await executeTextToImage(prompt, aspectRatio.value, imageModel.value, imageResolution.value, isNew);
                break;
            case 'image-to-image':
                result = await executeImageToImage(prompt, selectedFiles, imageModel.value, imageResolution.value, isNew);
                break;
            case 'text-to-video':
                result = await executeTextToVideo(prompt, videoResolution.value, videoAspectRatio.value);
                break;
            case 'image-to-video':
                result = await executeImageToVideo(prompt, selectedFiles, videoResolution.value, videoAspectRatio.value);
                break;
            case 'text-to-speech':
                result = await executeTextToSpeech(prompt, voiceName.value);
                break;
        }
        
        if (result && result.status === 'success') {
            // 세션 ID 저장 (Text to Image, Image to Image인 경우)
            if ((operation === 'text-to-image' || operation === 'image-to-image') && result.session_id) {
                lastImageSessionId = result.session_id;
                log(`세션 ID 저장됨: ${lastImageSessionId}`);
                updateSessionUI();
            }
            
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
        newSessionBtn.disabled = false;
        progressAlert.classList.add('d-none');
    }
}

// 실행 버튼 (Multi-turn 모드 또는 새로 만들기)
executeBtn.addEventListener('click', async () => {
    const operation = operationType.value;
    const isImageOperation = (operation === 'text-to-image' || operation === 'image-to-image');
    
    // 이미지 작업이고 세션이 있으면 Multi-turn 모드 (isNew=false)
    // 세션이 없으면 새로 만들기 모드 (isNew=true)
    const isNew = !isImageOperation || !lastImageSessionId;
    await executeOperation(isNew);
});

// 새로 만들기 버튼 (세션 초기화 후 실행)
newSessionBtn.addEventListener('click', async () => {
    // 세션 초기화
    lastImageSessionId = null;
    updateSessionUI();
    
    // 새로 만들기 모드로 실행
    await executeOperation(true);
});

// API 호출 함수들
async function executeTextToImage(prompt, aspectRatio, model, resolution, isNew = true) {
    const formData = new FormData();
    formData.append('prompt', prompt);
    formData.append('aspect_ratio', aspectRatio);
    formData.append('model', model);
    formData.append('resolution', resolution);
    formData.append('is_new', isNew);
    
    // Multi-turn 모드: 세션 ID 전달
    if (!isNew && lastImageSessionId) {
        formData.append('session_id', lastImageSessionId);
        log(`Multi-turn 모드: 세션 ${lastImageSessionId} 사용`);
    }
    
    const response = await fetch('/api/text-to-image', {
        method: 'POST',
        body: formData
    });
    
    let result;
    try {
        result = await response.json();
    } catch (error) {
        const text = await response.text();
        const parseError = new Error('작업 실패: 응답 형식이 JSON이 아닙니다.');
        parseError.details = text || error.message;
        throw parseError;
    }
    
    if (!response.ok) {
        const error = new Error(result.detail || '작업 실패');
        error.details = result.detail;
        throw error;
    }
    
    return result;
}

async function executeImageToImage(prompt, files, model, resolution, isNew = true) {
    const formData = new FormData();
    formData.append('prompt', prompt);
    formData.append('model', model);
    formData.append('resolution', resolution);
    formData.append('is_new', isNew);
    
    // Multi-turn 모드: 세션 ID 전달 (파일은 전송하지 않음)
    if (!isNew && lastImageSessionId) {
        formData.append('session_id', lastImageSessionId);
        log(`Multi-turn 모드: 세션 ${lastImageSessionId} 사용 (이미지 없이 프롬프트만 전송)`);
    } else {
        // 새로 만들기 모드: 멀티 파일 업로드
        files.forEach((file, index) => {
            formData.append('files', file);
        });
    }
    
    const response = await fetch('/api/image-to-image', {
        method: 'POST',
        body: formData
    });
    
    let result;
    try {
        result = await response.json();
    } catch (error) {
        const text = await response.text();
        const parseError = new Error('작업 실패: 응답 형식이 JSON이 아닙니다.');
        parseError.details = text || error.message;
        throw parseError;
    }
    
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
    
    // AbortController로 타임아웃 설정 (10분)
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 600000); // 10분
    
    try {
        const response = await fetch('/api/text-to-video', {
            method: 'POST',
            body: formData,
            signal: controller.signal
        });
        clearTimeout(timeoutId);
        
        const result = await response.json();
        
        if (!response.ok) {
            const error = new Error(result.detail || '작업 실패');
            error.details = result.detail;
            throw error;
        }
        
        // 비디오 UUID 및 해상도 저장 (확장 기능용)
        log(`Response video_uuid: ${result.video_uuid}`);
        if (result.video_uuid) {
            lastGeneratedVideoUUID = result.video_uuid;
            lastVideoResolution = resolution;
            log(`Saved video UUID: ${lastGeneratedVideoUUID}, resolution: ${lastVideoResolution}`);
        } else {
            log(`No video_uuid in response`);
        }
        
        return result;
    } catch (error) {
        clearTimeout(timeoutId);
        throw error;
    }
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
    
    // AbortController로 타임아웃 설정 (10분)
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 600000); // 10분
    
    try {
        const response = await fetch('/api/image-to-video', {
            method: 'POST',
            body: formData,
            signal: controller.signal
        });
        clearTimeout(timeoutId);
        
        const result = await response.json();
        
        if (!response.ok) {
            const error = new Error(result.detail || '작업 실패');
            error.details = result.detail;
            throw error;
        }
        
        // 비디오 UUID 및 해상도 저장 (확장 기능용)
        log(`Response video_uuid: ${result.video_uuid}`);
        if (result.video_uuid) {
            lastGeneratedVideoUUID = result.video_uuid;
            lastVideoResolution = resolution;
            log(`Saved video UUID: ${lastGeneratedVideoUUID}, resolution: ${lastVideoResolution}`);
        } else {
            log(`No video_uuid in response`);
        }
        
        return result;
    } catch (error) {
        clearTimeout(timeoutId);
        throw error;
    }
}

async function executeVideoExtension(prompt, videoUUID, resolution, aspectRatio) {
    log('비디오 확장 중... (시간이 다소 걸릴 수 있습니다)');
    
    const formData = new FormData();
    formData.append('prompt', prompt);
    formData.append('video_uuid', videoUUID);
    formData.append('resolution', resolution);
    formData.append('aspect_ratio', aspectRatio);
    
    // AbortController로 타임아웃 설정 (10분)
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 600000); // 10분
    
    try {
        const response = await fetch('/api/extend-video', {
            method: 'POST',
            body: formData,
            signal: controller.signal
        });
        clearTimeout(timeoutId);
        
        const result = await response.json();
        
        if (!response.ok) {
            const error = new Error(result.detail || '작업 실패');
            error.details = result.detail;
            throw error;
        }
        
        // 확장된 비디오 UUID 및 해상도 저장 (반복 확장 가능)
        log(`Response extended video_uuid: ${result.video_uuid}`);
        if (result.video_uuid) {
            lastGeneratedVideoUUID = result.video_uuid;
            // 확장 시 해상도는 동일하게 유지됨
            log(`Saved extended video UUID: ${lastGeneratedVideoUUID}, resolution: ${lastVideoResolution}`);
        }
        
        return result;
    } catch (error) {
        clearTimeout(timeoutId);
        throw error;
    }
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
    
    // 텍스트 전용 응답인 경우 (이미지 생성이 아닌 질문/번역 등)
    if (result.text_only) {
        resultContent.innerHTML = `
            <div class="alert alert-info text-start">
                <h6 class="alert-heading"><i class="bi bi-chat-dots"></i> 텍스트 응답</h6>
                <hr>
                <p class="mb-0" style="white-space: pre-wrap;">${result.llm_response}</p>
            </div>
        `;
        return;
    }
    
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
            <div class="mb-3">
                <a href="${result.output_file}" download class="btn btn-primary">
                    <i class="bi bi-download"></i> 다운로드
                </a>
            </div>
        `;
        
        // 비디오 확장 기능 추가 (Text to Video, Image to Video인 경우)
        if (operation === 'text-to-video' || operation === 'image-to-video') {
            // 720p인 경우만 확장 가능
            if (lastVideoResolution === '720p') {
                content += `
                    <div class="card mt-3">
                        <div class="card-header bg-info text-white">
                            <h6 class="mb-0"><i class="bi bi-arrow-right-circle"></i> 비디오 확장</h6>
                        </div>
                        <div class="card-body">
                            <p class="text-muted small mb-2">추가 프롬프트를 입력하여 비디오를 확장할 수 있습니다. (최대 141초까지 반복 가능, 한 번에 약 7초씩 확장)</p>
                            <textarea class="form-control mb-2" id="extendPrompt" rows="3" 
                                placeholder="확장할 내용에 대한 프롬프트를 입력하세요..."></textarea>
                            <button class="btn btn-info" id="extendVideoBtn">
                                <i class="bi bi-plus-circle"></i> 비디오 확장 실행
                            </button>
                        </div>
                    </div>
                `;
            } else if (lastVideoResolution === '1080p') {
                content += `
                    <div class="card mt-3">
                        <div class="card-header bg-warning text-dark">
                            <h6 class="mb-0"><i class="bi bi-exclamation-triangle"></i> 비디오 확장 불가</h6>
                        </div>
                        <div class="card-body">
                            <p class="text-muted small mb-0">
                                <i class="bi bi-info-circle"></i> 비디오 확장은 <strong>720p 해상도</strong>로 생성된 비디오만 지원됩니다.<br>
                                1080p로 생성된 비디오는 확장할 수 없습니다.<br>
                                비디오를 확장하려면 <strong>720p 해상도</strong>로 다시 생성해주세요.
                            </p>
                        </div>
                    </div>
                `;
            }
        }
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
    
    // 비디오 확장 버튼 이벤트 리스너 추가 (720p인 경우만)
    if (fileType === 'video' && (operation === 'text-to-video' || operation === 'image-to-video') && lastVideoResolution === '720p') {
        const extendBtn = document.getElementById('extendVideoBtn');
        if (extendBtn) {
            extendBtn.addEventListener('click', handleVideoExtension);
        }
    }
}

// 비디오 확장 처리 함수
async function handleVideoExtension() {
    const extendPrompt = document.getElementById('extendPrompt').value.trim();
    
    if (!extendPrompt) {
        alert('확장할 내용에 대한 프롬프트를 입력하세요.');
        return;
    }
    
    log(`Current lastGeneratedVideoUUID: ${lastGeneratedVideoUUID}`);
    
    if (!lastGeneratedVideoUUID) {
        alert('확장할 비디오 정보가 없습니다.');
        return;
    }
    
    // UI 상태 변경
    executeBtn.disabled = true;
    document.getElementById('extendVideoBtn').disabled = true;
    progressAlert.classList.remove('d-none');
    progressMessage.textContent = '비디오 확장 중...';
    
    log('비디오 확장 작업 시작');
    
    try {
        const result = await executeVideoExtension(
            extendPrompt,
            lastGeneratedVideoUUID,
            videoResolution.value,
            videoAspectRatio.value
        );
        
        if (result && result.status === 'success') {
            log('비디오 확장 완료');
            
            // 결과 업데이트
            const operation = operationType.value;
            displayResult(result, operation);
            
            // 확장 프롬프트 초기화
            document.getElementById('extendPrompt').value = '';
        }
    } catch (error) {
        if (error.details) {
            logError(`비디오 확장 오류:\n${error.details}`);
        } else {
            logError(`비디오 확장 오류: ${error.message}`);
        }
        alert('비디오 확장 실패: 자세한 내용은 로그를 확인하세요.');
    } finally {
        executeBtn.disabled = false;
        document.getElementById('extendVideoBtn').disabled = false;
        progressAlert.classList.add('d-none');
    }
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

