import os
import shutil
import threading
import subprocess
import sys
from typing import Optional

def copy_to_clipboard_async(filepath: str) -> None:
    """Copy file to clipboard in background thread."""
    def _copy():
        try:
            abs_path = os.path.abspath(filepath)
            if sys.platform == "win32":
                path = abs_path.replace('\\', '\\\\')
                subprocess.run(['powershell', '-Command', f'Set-Clipboard -Path "{path}"'], 
                             capture_output=True, check=False)
            elif sys.platform == "darwin":
                subprocess.run(['pbcopy'], input=abs_path.encode(), 
                             capture_output=True, check=False)
            else:
                subprocess.run(['xclip', '-selection', 'clipboard'], input=abs_path.encode(), 
                             capture_output=True, check=False)
        except Exception:
            pass  # Silently fail if clipboard operation fails

    # Start clipboard operation in background
    thread = threading.Thread(target=_copy)
    thread.daemon = True
    thread.start()

class FileHandler:
    @staticmethod
    def _get_next_sequential_filename(base_dir: str, is_audio_only: bool) -> str:
        """Generates a unique sequential filename like video1.mp4 or audio1.mp3."""
        base_name = "audio" if is_audio_only else "video"
        ext = "mp3" if is_audio_only else "mp4"
        counter = 1
        while True:
            filename = f"{base_name}{counter}.{ext}"
            full_path = os.path.join(base_dir, filename)
            if not os.path.exists(full_path):
                return full_path
            counter += 1

    @staticmethod
    def get_base_output_directory(is_audio_only: bool = False, is_thumbnail: bool = False, output_dir: Optional[str] = None) -> str:
        """Determine the base target directory based on content type."""
        base_dir = output_dir or os.path.expanduser('~/crec')
        
        if is_audio_only:
            target_dir = os.path.join(base_dir, 'audio')
        elif is_thumbnail:
            target_dir = os.path.join(base_dir, 'photos')
        else: # Default to video
            target_dir = os.path.join(base_dir, 'videos')
            
        os.makedirs(target_dir, exist_ok=True)
        return target_dir

    @staticmethod
    def get_output_path(url: str, output_dir: Optional[str] = None, filename_pattern: Optional[str] = None, 
                        is_audio_only: bool = False, is_thumbnail: bool = False, is_compressed: bool = False) -> str:
        """Generate the output template string for yt-dlp.
        This function returns a template string, not a final file path.
        """
        base_output_dir = FileHandler.get_base_output_directory(is_audio_only, is_thumbnail, output_dir)
        
        if filename_pattern:
            # Use the provided pattern directly with yt-dlp's templating, ensuring extension
            filename = f'{filename_pattern}.%(ext)s'
        elif is_thumbnail:
            # Default pattern for thumbnails
            filename = '%(title)s.%(ext)s'
        else:
            # Default sequential naming for videos/audio if no pattern is given
            # This path needs to be a concrete path, not a yt-dlp template
            return FileHandler._get_next_sequential_filename(base_output_dir, is_audio_only)

        if is_compressed:
            name, ext = os.path.splitext(filename)
            # Ensure the ext is part of the name for compression suffixing before %(ext)s is resolved
            # This might require a slight adjustment if yt-dlp's templating for %(ext)s is at the very end.
            # For simplicity, we'll append _compressed before yt-dlp handles the final extension.
            if filename_pattern: # If it was a custom pattern, apply suffix before %(ext)s
                filename = f'{filename_pattern}_compressed.%(ext)s'
            else: # If it was default yt-dlp pattern like %(title)s.%(ext)s
                filename = '%(title)s_compressed.%(ext)s'

        return os.path.join(base_output_dir, filename)
    
    @staticmethod
    def open_crec_directory():
        """Open the crec directory in the system's file explorer."""
        crec_dir = os.path.expanduser('~/crec')
        os.makedirs(crec_dir, exist_ok=True)
        
        if os.name == 'nt':  # Windows
            os.startfile(crec_dir)
        elif os.name == 'posix':  # macOS and Linux
            if sys.platform == 'darwin':  # macOS
                subprocess.run(['open', crec_dir])
            else:  # Linux
                subprocess.run(['xdg-open', crec_dir]) 