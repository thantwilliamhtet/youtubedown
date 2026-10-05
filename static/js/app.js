const analyzeForm = document.querySelector('#analyze-form');
const urlInput = document.querySelector('#video-url');
const inputWrap = document.querySelector('.input-wrap');
const formFeedback = document.querySelector('#form-feedback');
const analyzeButton = analyzeForm.querySelector('button[type="submit"]');
const mediaPanel = document.querySelector('#media-panel');
const mediaThumbnail = document.querySelector('#media-thumbnail');
const thumbnailFallback = document.querySelector('#thumbnail-fallback');
const chooseFolderButton = document.querySelector('#choose-folder-button');
const selectedDirectoryLabel = document.querySelector('#selected-directory');
const downloadOptionsPanel = document.querySelector('#download-options');
const downloadFormatNote = document.querySelector('#download-format-note');
const qualitySelect = document.querySelector('#quality-select');
const formatSelect = document.querySelector('#format-select');
const downloadButton = document.querySelector('#download-button');
const downloadFeedback = document.querySelector('#download-feedback');
let isAnalyzing = false;
let isChoosingFolder = false;
let isDownloading = false;
let analyzedUrl = null;
let selectedDirectory = null;
let downloadOptionsByFormat = {};

function getCanonicalYouTubeUrl(value) {
  const trimmedValue = value.trim();
  if (!trimmedValue) {
    return { error: 'Enter a YouTube video URL to continue.' };
  }

  let parsedUrl;
  try {
    parsedUrl = new URL(trimmedValue);
  } catch {
    return { error: 'Enter a complete URL, including https://.' };
  }

  if (!['http:', 'https:'].includes(parsedUrl.protocol) || parsedUrl.username || parsedUrl.password || parsedUrl.port) {
    return { error: 'Enter a valid YouTube video URL.' };
  }

  const hostname = parsedUrl.hostname.toLowerCase().replace(/\.$/, '');
  let videoId;

  if (hostname === 'youtu.be') {
    const pathParts = parsedUrl.pathname.split('/').filter(Boolean);
    if (pathParts.length === 1) videoId = pathParts[0];
  } else if (['youtube.com', 'www.youtube.com', 'm.youtube.com'].includes(hostname)) {
    if (parsedUrl.pathname === '/watch' && parsedUrl.searchParams.getAll('v').length === 1) {
      videoId = parsedUrl.searchParams.get('v');
    } else {
      const shortsMatch = parsedUrl.pathname.match(/^\/shorts\/([^/]+)\/?$/);
      if (shortsMatch) videoId = shortsMatch[1];
    }
  } else {
    return { error: 'Use a youtube.com or youtu.be video link.' };
  }

  if (!videoId || !/^[A-Za-z0-9_-]{11}$/.test(videoId)) {
    return { error: 'That link does not contain a valid YouTube video ID.' };
  }

  return { url: `https://www.youtube.com/watch?v=${videoId}` };
}

function setUrlFeedback(message, state) {
  formFeedback.textContent = message;
  formFeedback.className = `form-feedback is-${state}`;
  formFeedback.hidden = false;
  inputWrap.classList.toggle('is-invalid', state === 'error');
  inputWrap.classList.toggle('is-valid', state === 'success');
  inputWrap.classList.toggle('is-loading', state === 'loading');
  urlInput.setAttribute('aria-invalid', String(state === 'error'));
}

function formatDuration(seconds) {
  if (!Number.isFinite(seconds) || seconds < 0) return 'Duration unavailable';
  const totalSeconds = Math.floor(seconds);
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const remainder = totalSeconds % 60;
  return hours
    ? `${hours}:${String(minutes).padStart(2, '0')}:${String(remainder).padStart(2, '0')}`
    : `${minutes}:${String(remainder).padStart(2, '0')}`;
}

function formatFileSize(size) {
  if (!Number.isFinite(size) || size <= 0) return '';
  const units = ['B', 'KB', 'MB', 'GB'];
  let value = size;
  let unitIndex = 0;
  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024;
    unitIndex += 1;
  }
  return `${value.toFixed(unitIndex ? 1 : 0)} ${units[unitIndex]}`;
}

function renderFormats(formats) {
  const formatList = document.querySelector('#format-list');
  formatList.replaceChildren();
  (formats || []).forEach((format) => {
    const item = document.createElement('li');
    const parts = [format.format_id, format.ext?.toUpperCase()];
    if (format.resolution && format.resolution !== 'audio only') parts.push(format.resolution);
    if (format.fps) parts.push(`${format.fps} fps`);
    if (format.vcodec && format.vcodec !== 'none') parts.push(format.vcodec);
    if (format.acodec && format.acodec !== 'none') parts.push(format.acodec);
    const size = formatFileSize(format.filesize || format.filesize_approx);
    if (size) parts.push(size);
    item.textContent = parts.filter(Boolean).join(' · ');
    formatList.append(item);
  });
  document.querySelector('#format-detail-count').textContent = `(${formatList.children.length})`;
  document.querySelector('.format-details').hidden = formatList.children.length === 0;
}

function setMetadataText(selector, value, fallback = 'Not reported') {
  document.querySelector(selector).textContent = value || fallback;
}

function setThumbnailState(state) {
  mediaThumbnail.hidden = state !== 'loaded';
  thumbnailFallback.hidden = state === 'loaded';
  thumbnailFallback.textContent = state === 'loading'
    ? 'LOADING THUMBNAIL'
    : 'THUMBNAIL UNAVAILABLE';
}

function setDownloadFeedback(message, state) {
  downloadFeedback.textContent = message;
  downloadFeedback.className = `download-feedback is-${state}`;
  downloadFeedback.hidden = false;
}

function updateDownloadControls() {
  chooseFolderButton.disabled = isChoosingFolder || isDownloading;
  downloadButton.disabled = !analyzedUrl || !selectedDirectory || !qualitySelect.value || !formatSelect.value || isChoosingFolder || isDownloading;
}

function updateQualityOptions() {
  const qualities = downloadOptionsByFormat[formatSelect.value] || [];
  qualitySelect.replaceChildren(...qualities.map((quality) => {
    const option = document.createElement('option');
    option.value = quality;
    option.textContent = quality === 'best' ? 'Best Available' : quality;
    return option;
  }));
  updateDownloadControls();
}

function populateDownloadOptions(options, notice) {
  downloadOptionsByFormat = options && typeof options === 'object' ? options : {};
  downloadFormatNote.textContent = notice || '';
  downloadFormatNote.hidden = !notice;
  const formats = ['mp4', 'webm'].filter((format) => Array.isArray(downloadOptionsByFormat[format]) && downloadOptionsByFormat[format].length);
  formatSelect.replaceChildren(...formats.map((format) => {
    const option = document.createElement('option');
    option.value = format;
    option.textContent = format.toUpperCase();
    return option;
  }));

  downloadOptionsPanel.hidden = formats.length === 0;
  if (formats.length) {
    formatSelect.value = formats.includes('mp4') ? 'mp4' : formats[0];
    updateQualityOptions();
    qualitySelect.value = 'best';
  } else {
    qualitySelect.replaceChildren();
  }
  updateDownloadControls();
}

function loadThumbnail(thumbnail) {
  let thumbnailUrl;
  try {
    thumbnailUrl = new URL(thumbnail);
  } catch {
    setThumbnailState('unavailable');
    return;
  }

  if (thumbnailUrl.protocol !== 'https:') {
    setThumbnailState('unavailable');
    return;
  }

  const requestedUrl = thumbnailUrl.href;
  const showLoadedThumbnail = () => {
    if (mediaThumbnail.src !== requestedUrl) return;
    setThumbnailState(mediaThumbnail.naturalWidth > 0 ? 'loaded' : 'unavailable');
  };
  const showUnavailableThumbnail = () => {
    if (mediaThumbnail.src === requestedUrl) setThumbnailState('unavailable');
  };

  setThumbnailState('loading');
  mediaThumbnail.onload = showLoadedThumbnail;
  mediaThumbnail.onerror = showUnavailableThumbnail;
  mediaThumbnail.src = requestedUrl;

  if (mediaThumbnail.complete) showLoadedThumbnail();
}

function renderMetadata(metadata) {
  setMetadataText('#media-title', metadata.title, 'Untitled video');
  setMetadataText('#media-uploader', metadata.uploader, 'Unknown channel');
  setMetadataText('#media-duration', formatDuration(metadata.duration));
  setMetadataText('#media-id', `ID ${metadata.id}`);
  setMetadataText('#video-resolutions', metadata.video_resolutions?.join(' · '));
  setMetadataText('#audio-formats', metadata.audio_formats?.join(' · '));
  setMetadataText('#media-containers', metadata.containers?.map((container) => container.toUpperCase()).join(' · '));
  setMetadataText('#format-count', String(metadata.format_count ?? metadata.formats?.length ?? 0));
  renderFormats(metadata.formats);
  populateDownloadOptions(metadata.download_options, metadata.download_format_notice);

  mediaThumbnail.onload = null;
  mediaThumbnail.onerror = null;
  mediaThumbnail.removeAttribute('src');
  loadThumbnail(metadata.thumbnail);

  mediaPanel.hidden = false;
}

function clearMetadata() {
  mediaPanel.hidden = true;
  analyzedUrl = null;
  downloadOptionsByFormat = {};
  downloadOptionsPanel.hidden = true;
  downloadFormatNote.hidden = true;
  downloadFormatNote.textContent = '';
  qualitySelect.replaceChildren();
  formatSelect.replaceChildren();
  downloadFeedback.hidden = true;
  downloadFeedback.textContent = '';
  updateDownloadControls();
  mediaThumbnail.onload = null;
  mediaThumbnail.onerror = null;
  setThumbnailState('unavailable');
  mediaThumbnail.removeAttribute('src');
  document.querySelector('#format-list').replaceChildren();
}

analyzeForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  if (isAnalyzing || isChoosingFolder || isDownloading) return;

  const validation = getCanonicalYouTubeUrl(urlInput.value);
  if (validation.error) {
    clearMetadata();
    setUrlFeedback(validation.error, 'error');
    return;
  }

  isAnalyzing = true;
  clearMetadata();
  urlInput.value = validation.url;
  analyzeButton.disabled = true;
  analyzeButton.setAttribute('aria-busy', 'true');
  analyzeButton.classList.add('is-loading');
  analyzeButton.querySelector('span').textContent = 'Analyzing';
  setUrlFeedback('Checking YouTube for video details and available formats…', 'loading');

  try {
    const response = await fetch('/api/analyze', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url: validation.url }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Unable to analyze this video. Try again.');
    renderMetadata(result);
    analyzedUrl = validation.url;
    updateDownloadControls();
    setUrlFeedback('Analysis complete.', 'success');
  } catch (error) {
    const message = error instanceof TypeError
      ? 'Unable to reach the analysis service. Please check the app connection and try again.'
      : error.message;
    setUrlFeedback(message, 'error');
  } finally {
    isAnalyzing = false;
    analyzeButton.disabled = false;
    analyzeButton.removeAttribute('aria-busy');
    analyzeButton.classList.remove('is-loading');
    analyzeButton.querySelector('span').textContent = 'Analyze';
  }
});

chooseFolderButton.addEventListener('click', async () => {
  if (isChoosingFolder || isDownloading) return;

  isChoosingFolder = true;
  updateDownloadControls();
  setDownloadFeedback('Opening the folder picker…', 'loading');
  try {
    const response = await fetch('/api/choose-folder', { method: 'POST' });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Unable to open the folder picker.');
    if (result.cancelled) {
      setDownloadFeedback('Folder selection canceled.', 'error');
      return;
    }

    selectedDirectory = result.directory;
    selectedDirectoryLabel.textContent = selectedDirectory;
    selectedDirectoryLabel.title = selectedDirectory;
    setDownloadFeedback('Folder selected.', 'success');
  } catch (error) {
    const message = error instanceof TypeError
      ? 'Unable to reach the folder picker. Please check the app connection and try again.'
      : error.message;
    setDownloadFeedback(message, 'error');
  } finally {
    isChoosingFolder = false;
    updateDownloadControls();
  }
});

downloadButton.addEventListener('click', async () => {
  if (isDownloading || !analyzedUrl || !selectedDirectory || !qualitySelect.value || !formatSelect.value) return;

  isDownloading = true;
  urlInput.disabled = true;
  downloadButton.classList.add('is-loading');
  downloadButton.querySelector('span').textContent = 'Downloading';
  updateDownloadControls();
  setDownloadFeedback('Downloading video to the selected folder…', 'loading');

  try {
    const response = await fetch('/api/download', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        url: analyzedUrl,
        download_dir: selectedDirectory,
        quality: qualitySelect.value,
        format: formatSelect.value,
      }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Unable to download this video. Try again.');
    const size = formatFileSize(result.size);
    setDownloadFeedback(`Saved ${result.filename}${size ? ` · ${size}` : ''}.`, 'success');
  } catch (error) {
    const message = error instanceof TypeError
      ? 'Unable to reach the download service. Please check the app connection and try again.'
      : error.message;
    setDownloadFeedback(message, 'error');
  } finally {
    isDownloading = false;
    urlInput.disabled = false;
    downloadButton.classList.remove('is-loading');
    downloadButton.querySelector('span').textContent = 'Download';
    updateDownloadControls();
  }
});

formatSelect.addEventListener('change', updateQualityOptions);
qualitySelect.addEventListener('change', updateDownloadControls);

urlInput.addEventListener('input', () => {
  formFeedback.hidden = true;
  formFeedback.textContent = '';
  inputWrap.classList.remove('is-invalid', 'is-valid');
  urlInput.removeAttribute('aria-invalid');
  clearMetadata();
});
