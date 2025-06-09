import os
import yt_dlp
import subprocess
import sys
from typing import Optional, List, Dict
from ..utils.quality import QualityHandler
from ..utils.notify import Notifier
from ..utils.file_handler import FileHandler, copy_to_clipboard_async

class GenericHandler:
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
        """Check if the URL is a valid video URL."""
        return True  # Generic handler accepts any URL

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

    def download(self, url: str, audio_only: bool = False, quality: Optional[str] = None, 
                compress_level: int = 0, output_dir: Optional[str] = None, 
                download_thumbnail: bool = False, filename_pattern: Optional[str] = None,
                is_playlist: bool = False, ffmpeg_args: Optional[str] = None,
                no_audio: bool = False, no_original: bool = False, copy_to_clipboard: bool = True) -> Optional[str]:
        """Download a video from any supported source."""
        try:
            # Reset progress
            self.current_progress = 0
            
            # Get video info for custom naming
            video_info = self._get_video_info(url)
            if not video_info:
                print("Error: Could not get video information")
                return None

            # Get format ID for requested quality
            format_id = None
            if quality:
                try:
                    target_quality = int(quality)
                    format_id = QualityHandler.get_format_for_quality(url, target_quality)
                    if not format_id:
                        print(f"Quality {quality}p not available.")
                        formats = QualityHandler.get_available_qualities(url)
                        QualityHandler.list_qualities(formats)
                        return None
                except ValueError:
                    print(f"Invalid quality format: {quality}")
                    return None

            # Get the final output path
            final_output_path = self._get_next_filename(
                audio_only, output_dir, filename_pattern, video_info
            )

            # Update options based on special flags
            if audio_only:
                self.ydl_opts.update({
                    'format': 'bestaudio/best',
                    'postprocessors': [{
                        'key': 'FFmpegExtractAudio',
                        'preferredcodec': 'mp3',
                        'preferredquality': '192',
                    }],
                    'postprocessor_args': [
                        '-vn',  # No video
                        '-acodec', 'libmp3lame',  # Use MP3 codec
                        '-q:a', '2',  # High quality audio
                    ],
                })
                # For audio, we need to specify the output template without extension
                output_path = final_output_path.replace('.mp3', '')
            else:
                if no_audio:
                    # Download video without audio
                    self.ydl_opts.update({
                        'format': 'bestvideo[ext=mp4]/best[ext=mp4]',
                        'postprocessors': [],
                    })
                else:
                    # Normal video download
                    self.ydl_opts.update({
                        'format': format_id if format_id else 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
                        'postprocessors': [],
                    })
                output_path = final_output_path

            # Add custom FFmpeg arguments if provided
            if ffmpeg_args:
                self.ydl_opts['postprocessor_args'] = ffmpeg_args.split()

            self.ydl_opts['outtmpl'] = output_path

            # Download the content
            with yt_dlp.YoutubeDL(self.ydl_opts) as ydl:
                ydl.download([url])

            # Handle compression if requested
            if compress_level > 0 and not audio_only:
                print("\nCompressing video...")
                output_file = output_path.replace('.mp4', '_compressed.mp4')
                if QualityHandler.compress_video(output_path, output_file, compress_level):
                    # Remove original file if requested
                    if no_original:
                        os.remove(output_path)
                    final_output_path = output_file
                else:
                    print("Compression failed, keeping original file")

            # Move file to correct directory based on extension
            final_output_path = FileHandler.move_to_correct_directory(final_output_path)

            # Copy path to clipboard if requested
            if copy_to_clipboard:
                copy_to_clipboard_async(final_output_path)
            
            return final_output_path

        except Exception as e:
            print(f"Error downloading video: {str(e)}")
            return None 