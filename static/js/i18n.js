// 다국어(i18n) 지원
// 언어 선택은 localStorage에 저장하고, 서버 오류 메시지/로그인 페이지 언어 결정을 위해 "lang" 쿠키에도 동기화한다.
// 정적 요소는 data-i18n / data-i18n-placeholder / data-i18n-title / data-i18n-alt 속성으로 번역한다.

const LANG_STORAGE_KEY = 'lang';
const SUPPORTED_LANGS = ['en', 'ko'];
const DEFAULT_LANG = 'en';

const I18N = {
    en: {
        'nav.galleryToggle': 'Show/hide gallery',
        'nav.logout': 'Logout',
        'nav.language': 'Language',
        'gallery.title': 'Generated Media',
        'gallery.hide': 'Hide',
        'gallery.empty': 'No media generated yet.',
        'op.title': 'Select Task',
        'file.title': 'Input Files (max {n})',
        'file.drop': 'Drag and drop files here, or',
        'file.choose': 'Choose Files',
        'file.selectedOne': 'Selected file: {name}',
        'file.selectedMany': 'Selected files: {n}',
        'settings.title': 'Settings',
        'settings.show': 'Show',
        'settings.hide': 'Hide',
        'settings.imageModel': 'Image Model',
        'settings.imageResolution': 'Image Resolution',
        'settings.imageRatio': 'Aspect Ratio',
        'settings.videoModel': 'Video Model',
        'settings.videoResolution': 'Video Resolution',
        'settings.videoRatio': 'Video Aspect Ratio',
        'settings.landscape': '16:9 (Landscape)',
        'settings.portrait': '9:16 (Portrait)',
        'settings.voice': 'Voice',
        'pricing.title': 'Estimated Cost',
        'pricing.source': 'Official pricing',
        'pricing.na': 'No pricing info',
        'pricing.perImage': '{price} / image',
        'pricing.imageDetail': 'Per output image · input text and reference images billed separately · as of {date}',
        'pricing.perMillionOutput': '{price} / 1M output tokens',
        'pricing.approxPerDuration': '~{price} / {sec}s',
        'pricing.omniDetailNoRate': 'Input {input} / 1M tokens · per-second rate for the selected resolution is not officially published · as of {date}',
        'pricing.omniDetail': '~{perSec} / sec · video output {output} / 1M tokens · {tps} tokens/sec at 720p · input {input} / 1M tokens · as of {date}',
        'pricing.perDuration': '{price} / {sec}s',
        'pricing.asOf': 'As of {date}',
        'pricing.videoDetail': '{perSec} / sec · audio included · billed only for generated video · as of {date}',
        'pricing.perMinute': '{price} / 1 min of output',
        'pricing.ttsDetail': '{model} · output {tps} tokens/sec · input text {input} / 1M tokens · as of {date}',
        'prompt.title': 'Prompt',
        'prompt.save': 'Save',
        'prompt.list': 'List',
        'prompt.placeholder': 'Enter your prompt... (any language is fine)',
        'prompt.modalTitle': 'Saved Prompts',
        'prompt.empty': 'No saved prompts.',
        'btn.run': 'Run',
        'btn.edit': 'Edit',
        'progress.processing': 'Processing...',
        'progress.videoGenerating': 'Generating video...',
        'progress.videoExtending': 'Extending video...',
        'result.title': 'Result',
        'result.textResponse': 'Text Response',
        'result.omniExtendTitle': 'Extend Scene',
        'result.veoExtendTitle': 'Extend Video',
        'result.omniExtendHelp': 'Currently {sec}s · extend 10s at a time, up to 40s in total.',
        'result.veoExtendHelp': 'Enter an additional prompt to extend the video. (Repeatable up to 141s, about 7s per extension)',
        'result.extendPlaceholder': 'Enter a prompt describing what to add...',
        'result.extendRun': 'Extend Video',
        'result.extendUnavailable': 'Video Extension Unavailable',
        'result.omniMaxReached': 'The Gemini Omni video has reached the maximum total length of 40 seconds.',
        'result.liteNoExtend': 'Veo 3.1 Lite does not support video extension.',
        'result.resolutionNoExtend': '{res} videos cannot be extended.',
        'result.currentResolution': 'Current resolution',
        'result.extendSupportNote': 'Video extension is supported only for <strong>720p</strong> videos from <strong>Veo 3.1 Standard or Fast</strong>.',
        'log.title': 'Log',
        'log.ready': 'Ready',
        'log.appReady': 'Web application ready',
        'log.configFailed': 'Failed to load model config, using defaults',
        'log.fileRemoved': 'File removed ({n} remaining)',
        'log.filesAdded': 'Files added: {added} (total: {total})',
        'log.maxFilesChanged': 'Max files changed: {n}',
        'log.videoFilesTrimmed': 'Input images trimmed to {n} due to the selected video model\'s limit.',
        'log.filesTrimmed': 'File count exceeded the limit and was trimmed to {n}.',
        'log.taskStart': 'Task started: {op} ({mode})',
        'log.sessionSaved': 'Session ID saved: {id}',
        'log.llmResponse': 'LLM response: {text}',
        'log.taskDone': 'Task completed',
        'log.error': 'Error: {msg}',
        'log.multiturn': 'Multi-turn mode: using session {id}',
        'log.multiturnNoImage': 'Multi-turn mode: using session {id} (sending prompt only, no images)',
        'log.videoGenerating': 'Generating video... (this may take a while)',
        'log.videoExtending': 'Extending video... (this may take a while)',
        'log.videoJobQueued': 'Video job queued: {id}',
        'log.extendStart': 'Video extension started',
        'log.extendDone': 'Video extension completed',
        'log.extendError': 'Video extension error: {msg}',
        'log.promptSaved': 'Prompt saved',
        'log.promptSaveError': 'Prompt save error: {msg}',
        'log.promptLoaded': 'Loaded prompt ID {id}',
        'log.promptDeleted': 'Prompt deleted',
        'log.promptListError': 'Prompt list load error: {msg}',
        'log.galleryFailed': 'Failed to load gallery',
        'log.baseImageLoaded': 'Base image loaded for {mode} editing: {name}',
        'log.editImageLoadFailed': 'Failed to load image for editing: {msg}',
        'log.mediaDeleted': 'Deleted {media}: {name}',
        'log.mediaDeleteFailed': 'Failed to delete {media}: {msg}',
        'alert.maxFiles': 'You can select up to {n} files.',
        'alert.enterPrompt': 'Please enter a prompt.',
        'alert.selectFile': 'Please select an input file.',
        'alert.taskFailed': 'Task failed. See the log for details.',
        'alert.enterExtendPrompt': 'Please enter a prompt for the extension.',
        'alert.noVideoToExtend': 'No video to extend.',
        'alert.extendFailed': 'Video extension failed. See the log for details.',
        'alert.enterPromptToSave': 'Please enter a prompt to save.',
        'alert.promptSaved': 'Prompt saved.',
        'alert.promptSaveError': 'An error occurred while saving the prompt.',
        'alert.promptDeleteError': 'An error occurred while deleting the prompt.',
        'alert.promptListError': 'An error occurred while loading the prompt list.',
        'alert.editImageLoadFailed': 'Could not load the image for editing.',
        'alert.mediaDeleteError': 'An error occurred while deleting the {media}.',
        'confirm.deletePrompt': 'Delete this prompt?',
        'confirm.deleteMedia': 'Delete this {media}?',
        'error.taskFailed': 'Task failed',
        'error.videoJobStart': 'Failed to start video job',
        'error.videoJobStatus': 'Failed to check video job status',
        'error.videoFailed': 'Video generation failed',
        'error.nonJson': 'Server returned {type} instead of JSON. HTTP {status}',
        'error.deleteFailed': 'Delete failed',
        'common.unknown': 'unknown',
        'common.download': 'Download',
        'common.remove': 'Remove',
        'viewer.image': 'Image',
        'viewer.video': 'Video',
        'viewer.editImage': 'Edit Image',
        'viewer.editVideo': 'Edit as Video',
        'viewer.delete': 'Delete',
        'viewer.originalAlt': 'Original image',
        'media.image': 'image',
        'media.video': 'video'
    },
    ko: {
        'nav.galleryToggle': '갤러리 보이기/숨기기',
        'nav.logout': '로그아웃',
        'nav.language': '언어',
        'gallery.title': '생성된 미디어',
        'gallery.hide': '숨기기',
        'gallery.empty': '아직 생성된 미디어가 없습니다.',
        'op.title': '작업 선택',
        'file.title': '입력 파일 (최대 {n}장)',
        'file.drop': '여기에 파일을 드래그앤드롭하거나',
        'file.choose': '파일 선택',
        'file.selectedOne': '선택된 파일: {name}',
        'file.selectedMany': '선택된 파일: {n}개',
        'settings.title': '설정',
        'settings.show': '보이기',
        'settings.hide': '숨기기',
        'settings.imageModel': '이미지 생성 모델',
        'settings.imageResolution': '이미지 해상도',
        'settings.imageRatio': '이미지 비율',
        'settings.videoModel': '비디오 생성 모델',
        'settings.videoResolution': '비디오 해상도',
        'settings.videoRatio': '비디오 비율',
        'settings.landscape': '16:9 (가로)',
        'settings.portrait': '9:16 (세로)',
        'settings.voice': '음성 선택',
        'pricing.title': '예상 비용',
        'pricing.source': '공식 가격표',
        'pricing.na': '가격 정보 없음',
        'pricing.perImage': '{price} / 장',
        'pricing.imageDetail': '출력 이미지 1장 기준 · 입력 텍스트와 참조 이미지는 별도 과금 · {date} 기준',
        'pricing.perMillionOutput': '{price} / 출력 100만 토큰',
        'pricing.approxPerDuration': '약 {price} / {sec}초',
        'pricing.omniDetailNoRate': '입력 {input} / 100만 토큰 · 선택 해상도의 초당 환산가는 공식 미제공 · {date} 기준',
        'pricing.omniDetail': '약 {perSec} / 초 · 비디오 출력 {output} / 100만 토큰 · 720p 초당 {tps} 토큰 · 입력 {input} / 100만 토큰 · {date} 기준',
        'pricing.perDuration': '{price} / {sec}초',
        'pricing.asOf': '{date} 기준',
        'pricing.videoDetail': '{perSec} / 초 · 오디오 포함 · 생성된 동영상만 과금 · {date} 기준',
        'pricing.perMinute': '{price} / 출력 1분',
        'pricing.ttsDetail': '{model} · 출력 {tps} 토큰/초 · 입력 텍스트 {input} / 100만 토큰 · {date} 기준',
        'prompt.title': '프롬프트 입력',
        'prompt.save': '저장',
        'prompt.list': '목록',
        'prompt.placeholder': '프롬프트를 입력하세요... (프롬프트는 한국어로 작성해도 됩니다)',
        'prompt.modalTitle': '프롬프트 목록',
        'prompt.empty': '저장된 프롬프트가 없습니다.',
        'btn.run': '실행하기',
        'btn.edit': '편집하기',
        'progress.processing': '처리 중...',
        'progress.videoGenerating': '비디오 생성 중...',
        'progress.videoExtending': '비디오 확장 중...',
        'result.title': '결과',
        'result.textResponse': '텍스트 응답',
        'result.omniExtendTitle': '장면 연장',
        'result.veoExtendTitle': '비디오 확장',
        'result.omniExtendHelp': '현재 {sec}초 · 한 번에 10초씩 최대 누적 40초까지 연장할 수 있습니다.',
        'result.veoExtendHelp': '추가 프롬프트를 입력하여 비디오를 확장할 수 있습니다. (최대 141초까지 반복 가능, 한 번에 약 7초씩 확장)',
        'result.extendPlaceholder': '확장할 내용에 대한 프롬프트를 입력하세요...',
        'result.extendRun': '비디오 확장 실행',
        'result.extendUnavailable': '비디오 확장 불가',
        'result.omniMaxReached': 'Gemini Omni 비디오가 최대 누적 길이인 40초에 도달했습니다.',
        'result.liteNoExtend': 'Veo 3.1 Lite는 비디오 확장을 지원하지 않습니다.',
        'result.resolutionNoExtend': '{res} 비디오는 확장할 수 없습니다.',
        'result.currentResolution': '현재 해상도',
        'result.extendSupportNote': '비디오 확장은 <strong>Veo 3.1 Standard 또는 Fast</strong> 모델의 <strong>720p 해상도</strong>만 지원합니다.',
        'log.title': '로그',
        'log.ready': '준비 완료',
        'log.appReady': 'Web application ready',
        'log.configFailed': '모델 설정 로드 실패, 기본값 사용',
        'log.fileRemoved': '파일 제거됨 (남은 파일: {n}개)',
        'log.filesAdded': '파일 추가됨: {added}개 (전체: {total}개)',
        'log.maxFilesChanged': '최대 파일 수 변경: {n}장',
        'log.videoFilesTrimmed': '선택한 비디오 모델의 제한에 따라 입력 이미지를 {n}장으로 조정했습니다.',
        'log.filesTrimmed': '파일 개수가 최대 제한을 초과하여 {n}개로 조정되었습니다.',
        'log.taskStart': '작업 시작: {op} ({mode})',
        'log.sessionSaved': '세션 ID 저장됨: {id}',
        'log.llmResponse': 'LLM 응답: {text}',
        'log.taskDone': '작업 완료',
        'log.error': '오류 발생: {msg}',
        'log.multiturn': 'Multi-turn 모드: 세션 {id} 사용',
        'log.multiturnNoImage': 'Multi-turn 모드: 세션 {id} 사용 (이미지 없이 프롬프트만 전송)',
        'log.videoGenerating': '비디오 생성 중... (시간이 다소 걸릴 수 있습니다)',
        'log.videoExtending': '비디오 확장 중... (시간이 다소 걸릴 수 있습니다)',
        'log.videoJobQueued': '비디오 작업 등록됨: {id}',
        'log.extendStart': '비디오 확장 작업 시작',
        'log.extendDone': '비디오 확장 완료',
        'log.extendError': '비디오 확장 오류: {msg}',
        'log.promptSaved': '프롬프트 저장됨',
        'log.promptSaveError': '프롬프트 저장 오류: {msg}',
        'log.promptLoaded': '프롬프트 ID {id} 불러옴',
        'log.promptDeleted': '프롬프트 삭제됨',
        'log.promptListError': '프롬프트 목록 불러오기 오류: {msg}',
        'log.galleryFailed': '갤러리 로드 실패',
        'log.baseImageLoaded': '{mode} 편집용 base 이미지 로드됨: {name}',
        'log.editImageLoadFailed': '편집용 이미지 로드 실패: {msg}',
        'log.mediaDeleted': '{media} 삭제됨: {name}',
        'log.mediaDeleteFailed': '{media} 삭제 실패: {msg}',
        'alert.maxFiles': '최대 {n}개의 파일만 선택할 수 있습니다.',
        'alert.enterPrompt': '프롬프트를 입력하세요.',
        'alert.selectFile': '입력 파일을 선택하세요.',
        'alert.taskFailed': '작업 실패: 자세한 내용은 로그를 확인하세요.',
        'alert.enterExtendPrompt': '확장할 내용에 대한 프롬프트를 입력하세요.',
        'alert.noVideoToExtend': '확장할 비디오 정보가 없습니다.',
        'alert.extendFailed': '비디오 확장 실패: 자세한 내용은 로그를 확인하세요.',
        'alert.enterPromptToSave': '저장할 프롬프트를 입력하세요.',
        'alert.promptSaved': '프롬프트가 저장되었습니다.',
        'alert.promptSaveError': '프롬프트 저장 중 오류가 발생했습니다.',
        'alert.promptDeleteError': '프롬프트 삭제 중 오류가 발생했습니다.',
        'alert.promptListError': '프롬프트 목록을 불러오는 중 오류가 발생했습니다.',
        'alert.editImageLoadFailed': '편집용 이미지를 불러오지 못했습니다.',
        'alert.mediaDeleteError': '{media} 삭제 중 오류가 발생했습니다.',
        'confirm.deletePrompt': '이 프롬프트를 삭제하시겠습니까?',
        'confirm.deleteMedia': '이 {media}를 삭제하시겠습니까?',
        'error.taskFailed': '작업 실패',
        'error.videoJobStart': '비디오 작업 시작 실패',
        'error.videoJobStatus': '비디오 작업 상태 조회 실패',
        'error.videoFailed': '비디오 생성 실패',
        'error.nonJson': '서버가 JSON 대신 {type} 응답을 반환했습니다. HTTP {status}',
        'error.deleteFailed': '삭제 실패',
        'common.unknown': '알 수 없음',
        'common.download': '다운로드',
        'common.remove': '삭제',
        'viewer.image': '이미지',
        'viewer.video': '비디오',
        'viewer.editImage': '이미지 편집',
        'viewer.editVideo': '비디오 편집',
        'viewer.delete': '삭제',
        'viewer.originalAlt': '원본 이미지',
        'media.image': '이미지',
        'media.video': '비디오'
    }
};

// 저장된 언어를 읽는다. localStorage > 쿠키 > 기본값(en) 순으로 사용한다.
function loadLang() {
    try {
        const stored = localStorage.getItem(LANG_STORAGE_KEY);
        if (SUPPORTED_LANGS.includes(stored)) {
            return stored;
        }
    } catch (e) {
        // 스토리지 접근이 차단된 환경에서는 쿠키/기본값을 사용한다.
    }
    const cookieLang = document.cookie.match(/(?:^|;\s*)lang=(\w+)/)?.[1];
    return SUPPORTED_LANGS.includes(cookieLang) ? cookieLang : DEFAULT_LANG;
}

let currentLang = loadLang();

// 현재 언어를 localStorage와 쿠키(서버 메시지 언어용)에 저장한다.
function persistLang() {
    try {
        localStorage.setItem(LANG_STORAGE_KEY, currentLang);
    } catch (e) {
        // 저장 실패 시 쿠키만으로 유지한다.
    }
    document.cookie = `lang=${currentLang}; path=/; max-age=31536000; SameSite=Lax`;
}

// 키에 해당하는 번역 문자열을 반환한다. {name} 형태의 자리표시자는 params 값으로 치환한다.
function t(key, params = {}) {
    const template = I18N[currentLang][key] ?? I18N[DEFAULT_LANG][key] ?? key;
    return template.replace(/\{(\w+)\}/g, (match, name) => (name in params ? params[name] : match));
}

// 요소에 번역 키를 기록하고 텍스트를 설정한다. 언어 변경 시 applyI18n()이 다시 번역한다.
function setI18n(el, key, params) {
    el.dataset.i18n = key;
    if (params) {
        el.dataset.i18nParams = JSON.stringify(params);
    } else {
        delete el.dataset.i18nParams;
    }
    el.textContent = t(key, params);
}

// data-i18n* 속성이 지정된 모든 요소를 현재 언어로 번역한다.
function applyI18n(root = document) {
    document.documentElement.lang = currentLang;
    root.querySelectorAll('[data-i18n]').forEach(el => {
        const params = el.dataset.i18nParams ? JSON.parse(el.dataset.i18nParams) : {};
        el.textContent = t(el.dataset.i18n, params);
    });
    root.querySelectorAll('[data-i18n-placeholder]').forEach(el => {
        el.placeholder = t(el.dataset.i18nPlaceholder);
    });
    root.querySelectorAll('[data-i18n-alt]').forEach(el => {
        el.alt = t(el.dataset.i18nAlt);
    });
    root.querySelectorAll('[data-i18n-title]').forEach(el => {
        const text = t(el.dataset.i18nTitle);
        // Bootstrap 툴팁이 초기화된 요소는 title 대신 data-bs-original-title을 사용한다.
        if (el.hasAttribute('data-bs-original-title')) {
            el.setAttribute('data-bs-original-title', text);
        } else {
            el.title = text;
        }
    });
}

// 언어를 변경하고 저장한 뒤 화면을 다시 번역한다.
function setLang(lang) {
    if (!SUPPORTED_LANGS.includes(lang)) {
        return;
    }
    currentLang = lang;
    persistLang();
    applyI18n();
}

persistLang();
applyI18n();
