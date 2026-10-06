"""Low-overhead local media preview generation for the Activity tab."""
from __future__ import annotations

import io
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError


@dataclass(frozen=True)
class PreviewResult:
    kind: str
    png: bytes | None
    message: str


def _image_preview(path: Path, max_size: tuple[int, int]) -> PreviewResult:
    try:
        with Image.open(path) as source:
            try:
                source.seek(0)
            except EOFError:
                pass
            # JPEG draft/thumbnail can reduce decode work before allocating a full RGB copy.
            try:
                source.draft('RGB', max_size)
            except (OSError, ValueError):
                pass
            source.thumbnail(max_size, Image.Resampling.LANCZOS)
            image = ImageOps.exif_transpose(source)
            image.thumbnail(max_size, Image.Resampling.LANCZOS)
            if image.mode not in {'RGB','RGBA'}:
                image = image.convert('RGB')
            output = io.BytesIO()
            image.save(output, format='PNG', optimize=True)
        return PreviewResult('image', output.getvalue(), 'Image preview')
    except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError):
        return PreviewResult('image', None, 'Image preview unavailable; use Open file to view it.')


def _video_preview(path: Path, max_size: tuple[int, int]) -> PreviewResult:
    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg:
        return PreviewResult('video', None, 'Video downloaded. Install ffmpeg for poster-frame previews, or use Open file.')
    width, height = max_size
    command = [
        ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin', '-ss', '0.5', '-i', str(path),
        '-frames:v', '1', '-vf', f'scale={width}:{height}:force_original_aspect_ratio=decrease',
        '-f', 'image2pipe', '-vcodec', 'png', 'pipe:1',
    ]
    try:
        completed = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                   timeout=8, check=False)
    except (OSError, subprocess.SubprocessError):
        return PreviewResult('video', None, 'Video preview unavailable; use Open file to play it.')
    if completed.returncode != 0 or not completed.stdout:
        return PreviewResult('video', None, 'Video preview unavailable; use Open file to play it.')
    # ffmpeg is constrained by the scale filter; validate the output as an image before handing it to Tk.
    try:
        with Image.open(io.BytesIO(completed.stdout)) as image:
            image.verify()
    except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError):
        return PreviewResult('video', None, 'Video preview unavailable; use Open file to play it.')
    return PreviewResult('video', completed.stdout, 'Video poster frame')


def build_preview(path: Path | str, kind: str, max_size: tuple[int, int] = (320, 240)) -> PreviewResult:
    path = Path(path)
    if not path.is_file():
        return PreviewResult(kind, None, 'File is not available for preview.')
    width = max(64, min(640, int(max_size[0])))
    height = max(64, min(480, int(max_size[1])))
    if kind in {'photo', 'gif'}:
        return _image_preview(path, (width, height))
    if kind == 'video':
        return _video_preview(path, (width, height))
    if kind == 'audio':
        return PreviewResult('audio', None, 'Audio downloaded. Use Open file to play it.')
    return PreviewResult(kind, None, 'Preview is not available for this file type.')
