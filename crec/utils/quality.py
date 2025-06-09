import subprocess
import json
import re
from typing import Optional, List, Dict
import os
from tqdm import tqdm
import yt_dlp

class QualityHandler:
    @staticmethod
    def get_available_qualities(url: str) -> List[Dict]:
        """Get available video qualities using yt-dlp."""
        try:
            with yt_dlp.YoutubeDL({'quiet': True, 'no_warnings': True}) as ydl:
                info = ydl.extract_info(url, download=False)
                if not info:
                    return []

                formats = []
                for fmt in info.get('formats', []):
                    if not fmt.get('height'):
                        continue
                        
                    filesize = None
                    if fmt.get('filesize'):
                        filesize = fmt['filesize']
                    elif fmt.get('filesize_approx'):
                        filesize = fmt['filesize_approx']
                    elif fmt.get('tbr') and info.get('duration'):
                        filesize = (fmt['tbr'] * 1000 * info['duration']) / 8
                    
                    formats.append({
                        'format_id': fmt['format_id'],
                        'height': fmt['height'],
                        'filesize': filesize,
                        'ext': fmt.get('ext', 'mp4')
                    })
                
                # Sort by height and remove duplicates
                unique_formats = {}
                for fmt in sorted(formats, key=lambda x: x['height'], reverse=True):
                    height = fmt['height']
                    if height not in unique_formats:
                        unique_formats[height] = fmt
                
                return list(unique_formats.values())
                
        except Exception as e:
            print(f"Error getting qualities: {str(e)}")
            return []

    @staticmethod
    def list_qualities(formats: List[Dict]) -> None:
        """List available video qualities in a compact format."""
        if not formats:
            print("No qualities available")
            return
        
        print("\n📺 Available Qualities:")
        print("─" * 40)
        for fmt in formats:
            height = fmt['height']
            filesize = fmt.get('filesize')
            if filesize:
                filesize_mb = filesize / (1024 * 1024)
                print(f"│ {height:4d}p │ {filesize_mb:6.1f} MB")
            else:
                print(f"│ {height:4d}p │ Size unknown")
        print("─" * 40)

    @staticmethod
    def get_format_for_quality(url: str, target_quality: int) -> Optional[str]:
        """Get the best format ID for the requested quality."""
        formats = QualityHandler.get_available_qualities(url)
        if not formats:
            return None

        # Get available heights
        heights = [fmt['height'] for fmt in formats]
        max_height = max(heights)
        min_height = min(heights)

        # Adjust target quality if needed
        if target_quality > max_height:
            print(f"⚠️  Requested quality {target_quality}p is higher than maximum available quality {max_height}p")
            print(f"📥 Will download {max_height}p instead")
            target_quality = max_height
        elif target_quality < min_height:
            print(f"⚠️  Requested quality {target_quality}p is lower than minimum available quality {min_height}p")
            print(f"📥 Will download {min_height}p instead")
            target_quality = min_height

        # Find the format for the target quality
        for fmt in formats:
            if fmt['height'] == target_quality:
                return fmt['format_id']

        return None

    @staticmethod
    def compress_video(input_path: str, output_path: str, compress_level: int = 1) -> bool:
        """Compress video using ffmpeg with progress bar."""
        if not os.path.exists(input_path):
            print(f"Error: Input file {input_path} does not exist")
            return False

        # Get video duration using ffprobe
        try:
            duration_cmd = [
                'ffprobe', 
                '-v', 'error',
                '-show_entries', 'format=duration',
                '-of', 'json',
                input_path
            ]
            duration_result = subprocess.run(duration_cmd, capture_output=True, text=True)
            duration = float(json.loads(duration_result.stdout)['format']['duration'])
        except Exception as e:
            print(f"Error getting video duration: {str(e)}")
            return False

        # Base compression settings
        crf = 28 if compress_level == 1 else 32
        preset = 'medium' if compress_level == 1 else 'faster'
        
        # Build ffmpeg command
        cmd = [
            'ffmpeg',
            '-i', input_path,
            '-c:v', 'libx264',
            '-crf', str(crf),
            '-preset', preset,
            '-c:a', 'aac',
            '-b:a', '128k',
            '-y',  # Overwrite output file
            output_path
        ]

        # Create progress bar
        pbar = tqdm(total=100, desc="Compressing", unit="%")
        last_progress = 0

        try:
            process = subprocess.Popen(
                cmd,
                stderr=subprocess.PIPE,
                universal_newlines=True
            )

            # Read ffmpeg output
            while True:
                line = process.stderr.readline()
                if not line and process.poll() is not None:
                    break

                # Parse time progress
                time_match = re.search(r'time=(\d+):(\d+):(\d+.\d+)', line)
                if time_match:
                    hours, minutes, seconds = map(float, time_match.groups())
                    current_time = hours * 3600 + minutes * 60 + seconds
                    progress = min(100, int((current_time / duration) * 100))
                    
                    # Update progress bar
                    if progress > last_progress:
                        pbar.update(progress - last_progress)
                        last_progress = progress

            pbar.close()
            return process.returncode == 0

        except Exception as e:
            print(f"Error during compression: {str(e)}")
            return False 