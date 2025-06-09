import os
import shutil
from typing import Optional

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
    def move_to_correct_directory(filepath: str, base_dir: Optional[str] = None) -> str:
        """Move file to the correct directory based on its extension."""
        if not os.path.exists(filepath):
            return filepath
            
        target_dir = FileHandler.get_target_directory(filepath, base_dir)
        filename = os.path.basename(filepath)
        new_path = os.path.join(target_dir, filename)
        
        # If file is already in correct location, return path
        if os.path.dirname(filepath) == target_dir:
            return filepath
            
        # If file exists in target location, add number suffix
        counter = 1
        while os.path.exists(new_path):
            name, ext = os.path.splitext(filename)
            new_path = os.path.join(target_dir, f"{name}_{counter}{ext}")
            counter += 1
            
        # Move file to correct location
        shutil.move(filepath, new_path)
        return new_path
    
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