from pathlib import Path
import re
import shutil
import tempfile
import threading
from urllib.parse import parse_qs, urlsplit

from flask import Flask, jsonify, render_template, request
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError


app = Flask(__name__)
VIDEO_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{11}$")
download_lock = threading.Lock()


def normalize_youtube_url(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Enter a YouTube video URL to continue.")

    try:
        parsed_url = urlsplit(value.strip())
        hostname = (parsed_url.hostname or "").lower().removesuffix(".")
        port = parsed_url.port
    except ValueError as error:
        raise ValueError("Enter a valid YouTube video URL.") from error

    if parsed_url.scheme not in {"http", "https"} or parsed_url.username or parsed_url.password or port:
        raise ValueError("Enter a valid YouTube video URL.")

    video_id = None
    if hostname == "youtu.be":
        path_parts = [part for part in parsed_url.path.split("/") if part]
        if len(path_parts) == 1:
            video_id = path_parts[0]
    elif hostname in {"youtube.com", "www.youtube.com", "m.youtube.com"}:
        if parsed_url.path == "/watch":
            query = parse_qs(parsed_url.query, keep_blank_values=True)
            if len(query.get("v", [])) == 1:
                video_id = query["v"][0]
        else:
            shorts_match = re.fullmatch(r"/shorts/([^/]+)/?", parsed_url.path)
            if shorts_match:
                video_id = shorts_match.group(1)
    else:
        raise ValueError("Use a youtube.com or youtu.be video link.")

    if not video_id or not VIDEO_ID_PATTERN.fullmatch(video_id):
        raise ValueError("That link does not contain a valid YouTube video ID.")

    return f"https://www.youtube.com/watch?v={video_id}"


def summarize_formats(formats):
    available_formats = []
    video_resolutions = set()
    audio_formats = set()
    containers = set()

    for media_format in formats or []:
        extension = media_format.get("ext")
        video_codec = media_format.get("vcodec")
        audio_codec = media_format.get("acodec")
        has_video = video_codec not in (None, "none")
        has_audio = audio_codec not in (None, "none")

        if media_format.get("ext") == "mhtml" or str(media_format.get("format_id", "")).startswith("sb"):
            continue

        if extension:
            containers.add(extension)
        if has_video and media_format.get("height"):
            video_resolutions.add(f"{media_format['height']}p")
        if has_audio:
            audio_label = f"{extension or 'unknown'} · {audio_codec}"
            if media_format.get("abr"):
                audio_label += f" · {round(media_format['abr'])} kbps"
            audio_formats.add(audio_label)

        if not has_video and not has_audio:
            continue

        available_formats.append({
            "format_id": media_format.get("format_id"),
            "ext": extension,
            "resolution": media_format.get("resolution"),
            "width": media_format.get("width"),
            "height": media_format.get("height"),
            "fps": media_format.get("fps"),
            "vcodec": video_codec,
            "acodec": audio_codec,
            "abr": media_format.get("abr"),
            "tbr": media_format.get("tbr"),
            "filesize": media_format.get("filesize"),
            "filesize_approx": media_format.get("filesize_approx"),
            "format_note": media_format.get("format_note"),
        })

    return {
        "formats": available_formats,
        "video_resolutions": sorted(video_resolutions, key=lambda item: int(item[:-1]), reverse=True),
        "audio_formats": sorted(audio_formats),
        "containers": sorted(containers),
    }


def available_download_options(formats):
    media_formats = [
        media_format for media_format in formats or []
        if media_format.get("ext") != "mhtml"
        and not str(media_format.get("format_id", "")).startswith("sb")
    ]
    has_ffmpeg = shutil.which("ffmpeg") is not None
    download_options = {}

    for container, video_extensions, audio_extensions in (
        ("mp4", {"mp4"}, {"m4a", "mp4"}),
        ("webm", {"webm"}, {"webm"}),
    ):
        container_formats = [item for item in media_formats if item.get("ext") == container]
        muxed_formats = [
            item for item in container_formats
            if item.get("vcodec") not in (None, "none") and item.get("acodec") not in (None, "none")
        ]
        video_formats = [
            item for item in media_formats
            if item.get("ext") in video_extensions and item.get("vcodec") not in (None, "none")
        ]
        audio_formats = [
            item for item in media_formats
            if item.get("ext") in audio_extensions and item.get("acodec") not in (None, "none")
        ]
        can_merge = has_ffmpeg and video_formats and audio_formats

        if not muxed_formats and not can_merge:
            continue

        selectable_formats = video_formats if can_merge else []
        heights = {
            item.get("height") for item in (selectable_formats + muxed_formats)
            if isinstance(item.get("height"), int)
        }
        qualities = ["best"] + [f"{height}p" for height in sorted(heights, reverse=True)]
        download_options[container] = qualities

    return download_options


def download_format_notice(formats):
    if shutil.which("ffmpeg"):
        return None

    media_formats = formats or []
    unavailable_containers = []
    for container, video_extensions, audio_extensions in (
        ("MP4", {"mp4"}, {"m4a", "mp4"}),
        ("WebM", {"webm"}, {"webm"}),
    ):
        container_formats = [item for item in media_formats if item.get("ext") == container.lower()]
        has_muxed = any(
            item.get("vcodec") not in (None, "none") and item.get("acodec") not in (None, "none")
            for item in container_formats
        )
        has_video = any(
            item.get("ext") in video_extensions and item.get("vcodec") not in (None, "none")
            for item in media_formats
        )
        has_audio = any(
            item.get("ext") in audio_extensions and item.get("acodec") not in (None, "none")
            for item in media_formats
        )
        if not has_muxed and has_video and has_audio:
            unavailable_containers.append(container)

    if unavailable_containers:
        formats_label = " and ".join(unavailable_containers)
        return f"FFmpeg is required to combine separate streams into {formats_label} for this video."
    return None


def build_download_selector(formats, quality, container):
    if not isinstance(container, str) or container not in {"mp4", "webm"}:
        raise ValueError("Choose MP4 or WebM as the output format.")
    if not isinstance(quality, str):
        raise ValueError("Choose an available video quality.")

    options = available_download_options(formats)
    if container not in options:
        raise ValueError(f"{container.upper()} is not available for this video with the installed media tools.")
    if quality not in options[container]:
        available = ", ".join("Best Available" if item == "best" else item for item in options[container])
        raise ValueError(f"{quality} is not available as {container.upper()}. Choose: {available}.")

    height_filter = "" if quality == "best" else f"[height={int(quality[:-1])}]"
    if container == "mp4":
        video_selector = f"bestvideo[ext=mp4]{height_filter}"
        audio_selector = "bestaudio[ext=m4a]/bestaudio[ext=mp4]"
    else:
        video_selector = f"bestvideo[ext=webm]{height_filter}"
        audio_selector = "bestaudio[ext=webm]"

    muxed_selector = f"best[ext={container}]{height_filter}[vcodec!=none][acodec!=none]"
    if shutil.which("ffmpeg"):
        return f"{video_selector}+{audio_selector}/{muxed_selector}"
    return muxed_selector


def user_facing_download_error(error):
    error_text = str(error).lower()
    restricted_terms = ("private", "members-only", "sign in", "age-restricted", "unavailable", "geo-restricted")
    if any(term in error_text for term in restricted_terms):
        return "This video is unavailable or restricted. Only publicly accessible videos can be analyzed."
    if any(term in error_text for term in ("timed out", "connection", "network", "temporary failure", "name or service not known")):
        return "A network error prevented analysis. Check your connection and try again."
    return "Unable to analyze this video. Check that the link is available and try again."


def validate_download_directory(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Choose a folder before downloading.")

    try:
        directory = Path(value).expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise ValueError("That folder does not exist or cannot be accessed. Choose another folder.") from error

    if not directory.is_dir():
        raise ValueError("The selected location is not a folder. Choose another folder.")

    try:
        with tempfile.NamedTemporaryFile(prefix=".local-downloader-check-", dir=directory, delete=True):
            pass
    except OSError as error:
        raise ValueError("The selected folder is not writable. Choose a folder with write access.") from error

    return directory


def user_facing_video_download_error(error):
    error_text = str(error).lower()
    restricted_terms = ("private", "members-only", "sign in", "age-restricted", "unavailable", "geo-restricted")
    if any(term in error_text for term in restricted_terms):
        return "This video is unavailable or restricted. Only publicly accessible videos can be downloaded."
    if any(term in error_text for term in ("timed out", "connection", "network", "temporary failure", "name or service not known")):
        return "A network error interrupted the download. Check your connection and try again."
    return "Unable to download this video. Check that the link is available and try again."


@app.get("/")
def index():
    return render_template("index.html")


@app.post("/api/analyze")
def analyze():
    payload = request.get_json(silent=True)
    try:
        video_url = normalize_youtube_url(payload.get("url") if isinstance(payload, dict) else None)
    except ValueError as error:
        return jsonify({"error": str(error)}), 400

    try:
        options = {
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "skip_download": True,
            "cachedir": False,
            "socket_timeout": 15,
            "retries": 1,
        }
        with YoutubeDL(options) as youtube_dl:
            info = youtube_dl.extract_info(video_url, download=False)

        if not isinstance(info, dict):
            return jsonify({"error": "No video information was returned. Check the link and try again."}), 422

        metadata = summarize_formats(info.get("formats"))
        return jsonify({
            "id": info.get("id"),
            "title": info.get("title") or "Untitled video",
            "uploader": info.get("uploader") or info.get("channel") or "Unknown channel",
            "duration": info.get("duration"),
            "thumbnail": info.get("thumbnail"),
            "webpage_url": video_url,
            "format_count": len(metadata["formats"]),
            "download_options": available_download_options(info.get("formats")),
            "download_format_notice": download_format_notice(info.get("formats")),
            **metadata,
        })
    except DownloadError as error:
        app.logger.warning("Media analysis failed: %s", error)
        message = user_facing_download_error(error)
        status_code = 422 if "unavailable or restricted" in message else 502
        return jsonify({"error": message}), status_code
    except Exception:
        app.logger.exception("Unexpected error during media analysis")
        return jsonify({"error": "An unexpected error prevented analysis. Please try again."}), 500


@app.post("/api/choose-folder")
def choose_folder():
    root = None
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        selected_directory = filedialog.askdirectory(
            parent=root,
            title="Choose a download folder",
            initialdir=str(Path.home()),
            mustexist=True,
        )
    except Exception:
        app.logger.exception("Could not open the native folder picker")
        return jsonify({"error": "The folder picker could not be opened. Please try again in a desktop session."}), 500
    finally:
        if root is not None:
            root.destroy()

    if not selected_directory:
        return jsonify({"cancelled": True})

    try:
        directory = validate_download_directory(selected_directory)
    except ValueError as error:
        return jsonify({"error": str(error)}), 400

    return jsonify({"directory": str(directory)})


@app.post("/api/download")
def download():
    payload = request.get_json(silent=True)
    try:
        video_url = normalize_youtube_url(payload.get("url") if isinstance(payload, dict) else None)
        directory_value = payload.get("download_dir", payload.get("directory")) if isinstance(payload, dict) else None
        directory = validate_download_directory(directory_value)
    except ValueError as error:
        return jsonify({"error": str(error)}), 400

    quality = payload.get("quality", "best") if isinstance(payload, dict) else "best"
    container = payload.get("format", "webm") if isinstance(payload, dict) else "webm"
    if not isinstance(container, str) or container not in {"mp4", "webm"}:
        return jsonify({"error": "Choose MP4 or WebM as the output format."}), 400
    if not isinstance(quality, str) or (quality != "best" and not re.fullmatch(r"\d{3,4}p", quality)):
        return jsonify({"error": "Choose Best Available or a listed video quality."}), 400

    if not download_lock.acquire(blocking=False):
        return jsonify({"error": "A download is already in progress. Please wait for it to finish."}), 409

    try:
        metadata_options = {
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "skip_download": True,
            "cachedir": False,
            "socket_timeout": 15,
            "retries": 1,
        }
        with YoutubeDL(metadata_options) as metadata_downloader:
            metadata = metadata_downloader.extract_info(video_url, download=False)
        selector = build_download_selector(metadata.get("formats"), quality, container)
        quality_suffix = "" if quality == "best" else f" - {quality}"

        options = {
            **metadata_options,
            "format": selector,
            "merge_output_format": container,
            "skip_download": False,
            "outtmpl": str(directory / f"%(title)s [%(id)s]{quality_suffix}.%(ext)s"),
            "nooverwrites": True,
        }
        with YoutubeDL(options) as youtube_dl:
            info = youtube_dl.extract_info(video_url, download=True)
            candidates = [
                Path(item["filepath"])
                for item in info.get("requested_downloads", [])
                if item.get("filepath")
            ]
            if info.get("_filename"):
                candidates.append(Path(info["_filename"]))
            candidates.append(Path(youtube_dl.prepare_filename(info)))

        output_file = next((candidate for candidate in candidates if candidate.is_file()), None)
        if output_file is None:
            return jsonify({"error": "The download finished without a file being created. Try again."}), 500

        return jsonify({
            "success": True,
            "filename": output_file.name,
            "size": output_file.stat().st_size,
        })
    except ValueError as error:
        return jsonify({"error": str(error)}), 400
    except DownloadError as error:
        app.logger.warning("Video download failed: %s", error)
        return jsonify({"error": user_facing_video_download_error(error)}), 422
    except OSError:
        app.logger.exception("Could not save the downloaded video")
        return jsonify({"error": "The video could not be saved to that folder. Check its write access and available space."}), 500
    except Exception:
        app.logger.exception("Unexpected error during video download")
        return jsonify({"error": "An unexpected error prevented the download. Please try again."}), 500
    finally:
        download_lock.release()


if __name__ == "__main__":
    # The native folder dialog must run on the server's main thread.
    app.run(host="127.0.0.1", port=5000, debug=False, threaded=False)
