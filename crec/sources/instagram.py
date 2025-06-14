import os
import yt_dlp
import subprocess
import sys
from typing import Optional, List, Dict
from ..utils.quality import QualityHandler
from ..utils.notify import Notifier
from ..utils.file_handler import FileHandler, copy_to_clipboard_async

def copy_file_to_clipboard(filepath):
    """Copy file path to clipboard (cross-platform)"""
    abs_path = os.path.abspath(filepath)
    
    if sys.platform == "win32":
        path = abs_path.replace('\\', '\\\\')
        os.system(f'powershell Set-Clipboard -Path "{path}"')
    elif sys.platform == "darwin":
        os.system(f"echo '{abs_path}' | pbcopy")
    else:
        os.system(f"echo '{abs_path}' | xclip -selection clipboard")

class InstagramHandler:
    def __init__(self):
        self.download_dir = os.path.expanduser('~/crec/videos')
        self.audio_dir = os.path.expanduser('~/crec/audio')
        self.thumbnail_dir = os.path.expanduser('~/crec/photos')
        os.makedirs(self.download_dir, exist_ok=True)
        os.makedirs(self.audio_dir, exist_ok=True)
        os.makedirs(self.thumbnail_dir, exist_ok=True)
        
        # Default best quality
        self.ydl_opts = {
            'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
            'quiet': True,
            'no_warnings': True,
            'progress_hooks': [self._progress_hook],
            'nocheckcertificate': True,
            'ignoreerrors': True,
            'no_color': True,
            'extract_flat': False,
        }
        self.current_progress = 0

    def can_handle(self, url: str) -> bool:
        """Check if the URL is a valid Instagram URL."""
        return 'instagram.com' in url

    def _get_next_filename(self, audio_only: bool = False, output_dir: Optional[str] = None, 
                          filename_pattern: Optional[str] = None, video_info: Optional[Dict] = None) -> str:
        """Get the next available filename."""
        base_name = "audio" if audio_only else "video"
        target_dir = output_dir or (self.audio_dir if audio_only else self.download_dir)
        
        if filename_pattern and video_info:
            # Replace placeholders in filename pattern
            filename = filename_pattern
            filename = filename.replace('{title}', video_info.get('title', 'video'))
            filename = filename.replace('{id}', video_info.get('id', ''))
            filename = filename.replace('{quality}', str(video_info.get('height', '')))
            filename = filename.replace('{date}', video_info.get('upload_date', ''))
            # Remove invalid characters
            filename = "".join(c for c in filename if c.isalnum() or c in (' ', '-', '_', '.'))
            ext = "mp3" if audio_only else "mp4"
            return os.path.join(target_dir, f"{filename}.{ext}")
        
        counter = 1
        while True:
            ext = "mp3" if audio_only else "mp4"
            filename = f"{base_name}{counter}.{ext}"
            if not os.path.exists(os.path.join(target_dir, filename)):
                return os.path.join(target_dir, filename)
            counter += 1

    def _progress_hook(self, d):
        """Handle download progress."""
        if d['status'] == 'downloading':
            try:
                total = d.get('total_bytes', 0) or d.get('total_bytes_estimate', 0)
                downloaded = d.get('downloaded_bytes', 0)
                if total > 0:
                    progress = (downloaded / total) * 100
                    if progress > self.current_progress:
                        print(f"\rDownloading: {progress:.1f}%", end='', flush=True)
                        self.current_progress = progress
            except:
                pass
        elif d['status'] == 'finished':
            print("\nDownload completed, processing...")

    def _get_video_info(self, url: str) -> Optional[Dict]:
        """Get video information."""
        try:
            with yt_dlp.YoutubeDL({
                'quiet': True, 
                'no_warnings': True,
                'nocheckcertificate': True,
                'ignoreerrors': True,
            }) as ydl:
                return ydl.extract_info(url, download=False)
        except:
            return None

    def download(self, url: str, audio_only: bool, quality: Optional[str], 
                compress_level: int, output_dir: Optional[str], 
                download_thumbnail: bool, filename_pattern: Optional[str], is_playlist: bool,
                ffmpeg_args: Optional[str], no_audio: bool, copy_to_clipboard: bool) -> Optional[str]:

        outtmpl = FileHandler.get_output_path(url, output_dir, filename_pattern, audio_only, download_thumbnail)

        ydl_opts = {
            'format': 'bestvideo+bestaudio/best' if not audio_only else 'bestaudio/best',
            'outtmpl': outtmpl,
            'nocheckcertificate': True,
            'ignoreerrors': True,
            'postprocessors': [],
            'progress_hooks': [self.hook],
            'quiet': True,
        }

        if quality:
            format_id = QualityHandler.get_format_for_quality(url, int(quality))
            if format_id:
                ydl_opts['format'] = format_id + ('+bestaudio' if not audio_only else '')
            else:
                print(f"Warning: Quality {quality}p not found. Downloading best available.")

        if audio_only:
            ydl_opts['postprocessors'].append({
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            })
            ydl_opts['format'] = 'bestaudio/best'

        if no_audio:
            ydl_opts['format'] = 'bestvideo/best'

        if ffmpeg_args:
            ydl_opts['postprocessors'].append({
                'key': 'FFmpegVideoConvertor',
                'args': ffmpeg_args.split()
            })

        if download_thumbnail:
            ydl_opts['skip_download'] = True
            ydl_opts['writethumbnail'] = True

        if is_playlist:
            ydl_opts['extract_flat'] = 'in_playlist'
            if not filename_pattern:
                base_dir = FileHandler.get_base_output_directory(audio_only, download_thumbnail, output_dir)
                ydl_opts['outtmpl'] = os.path.join(base_dir, '%(playlist_index)s - %(title)s.%(ext)s')

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info_dict = ydl.extract_info(url, download=True)
                final_filepath = ydl.prepare_filename(info_dict)

            if compress_level > 0:
                compressed_filepath = FileHandler.get_output_path(url, output_dir, filename_pattern, is_compressed=True)
                if QualityHandler.compress_video(final_filepath, compressed_filepath, compress_level):
                    os.remove(final_filepath)
                    final_filepath = compressed_filepath

            if copy_to_clipboard and final_filepath:
                copy_to_clipboard_async(os.path.abspath(final_filepath))
                print(f"Path copied to clipboard: {os.path.abspath(final_filepath)}")

            return final_filepath

        except Exception as e:
            print(f"An error occurred: {e}")
            return None

    def hook(self, d):
        if d['status'] == 'downloading':
            pass 