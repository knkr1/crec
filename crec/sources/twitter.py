import os
import yt_dlp
import subprocess
import sys
from typing import Optional
from ..utils.quality import QualityHandler

def copy_file_to_clipboard(filepath):
    """Copy file path to clipboard based on OS."""
    if sys.platform == 'win32':
        os.system(f'echo {filepath} | clip')
    elif sys.platform == 'darwin':
        os.system(f'echo {filepath} | pbcopy')
    else:
        os.system(f'echo {filepath} | xclip -selection clipboard')

class TwitterHandler:
    def __init__(self):
        self.download_dir = os.path.expanduser('~/crec/videos')
        os.makedirs(self.download_dir, exist_ok=True)
        
        # Default best quality
        self.ydl_opts = {
            'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
            'outtmpl': os.path.join(self.download_dir, '%(title)s.%(ext)s'),
            'quiet': True,
            'no_warnings': True,
            'progress_hooks': [self._progress_hook],
        }
        self.current_progress = 0

    def can_handle(self, url: str) -> bool:
        """Check if the URL is a valid Twitter URL."""
        return 'twitter.com' in url or 'x.com' in url

    def _get_next_filename(self, audio_only: bool = False) -> str:
        """Get the next available filename."""
        base_name = "twitter_video"
        if audio_only:
            base_name = "twitter_audio"
        
        counter = 1
        while True:
            ext = "mp3" if audio_only else "mp4"
            filename = f"{base_name}_{counter}.{ext}"
            if not os.path.exists(os.path.join(self.download_dir, filename)):
                return filename
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

    def download(self, url: str, audio_only: bool = False, quality: Optional[str] = None, compress_level: int = 0) -> Optional[str]:
        """Download a Twitter video."""
        try:
            # Reset progress
            self.current_progress = 0
            
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

            # Update options
            self.ydl_opts['format'] = format_id if format_id else 'bestaudio[ext=m4a]' if audio_only else 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best'
            self.ydl_opts['outtmpl'] = os.path.join(self.download_dir, self._get_next_filename(audio_only))

            # Download the video
            with yt_dlp.YoutubeDL(self.ydl_opts) as ydl:
                ydl.download([url])

            # Get the downloaded file path
            downloaded_file = self.ydl_opts['outtmpl']

            # Handle compression if requested
            if compress_level > 0 and not audio_only:
                print("\nCompressing video...")
                output_file = downloaded_file.replace('.mp4', '_compressed.mp4')
                if QualityHandler.compress_video(downloaded_file, output_file, compress_level):
                    # Remove original file
                    os.remove(downloaded_file)
                    downloaded_file = output_file
                else:
                    print("Compression failed, keeping original file")

            # Copy path to clipboard
            copy_file_to_clipboard(downloaded_file)
            return downloaded_file

        except Exception as e:
            print(f"Error downloading video: {str(e)}")
            return None 