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
    # Define file type mappings
    VIDEO_EXTENSIONS = {'.mp4', '.mkv', '.avi', '.mov', '.wmv', '.flv', '.webm'}
    AUDIO_EXTENSIONS = {'.mp3', '.wav', '.ogg', '.m4a', '.flac', '.aac'}
    PHOTO_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.tiff', '.ico'}
    
    @staticmethod
    def get_target_directory(filepath: str, base_dir: Optional[str] = None) -> str:
        """Determine the target directory based on file extension."""
        base_dir = base_dir or os.path.expanduser('~/crec')
        ext = os.path.splitext(filepath)[1].lower()
        
        if ext in FileHandler.VIDEO_EXTENSIONS:
            target_dir = os.path.join(base_dir, 'videos')
        elif ext in FileHandler.AUDIO_EXTENSIONS:
            target_dir = os.path.join(base_dir, 'audio')
        elif ext in FileHandler.PHOTO_EXTENSIONS:
            target_dir = os.path.join(base_dir, 'photos')
        else:
            target_dir = os.path.join(base_dir, 'other')
            
        os.makedirs(target_dir, exist_ok=True)
        return target_dir
    
    @staticmethod
    def move_to_correct_directory(filepath: str) -> str:
        """Move file to the correct directory based on its extension."""
        if not os.path.exists(filepath):
            return filepath

        # Get the file extension
        _, ext = os.path.splitext(filepath)
        ext = ext.lower()

        # Determine target directory based on extension
        if ext in ['.mp4', '.mkv', '.avi', '.mov', '.wmv']:
            target_dir = os.path.expanduser('~/crec/videos')
        elif ext in ['.mp3', '.wav', '.m4a', '.aac', '.flac']:
            target_dir = os.path.expanduser('~/crec/audio')
        elif ext in ['.jpg', '.jpeg', '.png', '.gif', '.webp']:
            target_dir = os.path.expanduser('~/crec/photos')
        else:
            return filepath

        # Create target directory if it doesn't exist
        os.makedirs(target_dir, exist_ok=True)

        # Get the filename
        filename = os.path.basename(filepath)
        target_path = os.path.join(target_dir, filename)

        # If file is already in the correct directory, return the path
        if os.path.dirname(filepath) == target_dir:
            return filepath

        # If target file already exists, add a number to the filename
        counter = 1
        while os.path.exists(target_path):
            name, ext = os.path.splitext(filename)
            target_path = os.path.join(target_dir, f"{name}_{counter}{ext}")
            counter += 1

        # Move the file
        try:
            shutil.move(filepath, target_path)
            return target_path
        except Exception:
            return filepath
    
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