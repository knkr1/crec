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
        """Get list of available qualities."""
        try:
            with yt_dlp.YoutubeDL({
                'quiet': True,
                'no_warnings': True,
                'nocheckcertificate': True,
                'ignoreerrors': True,
            }) as ydl:
                info = ydl.extract_info(url, download=False)
                if not info:
                    return []

                formats = info.get('formats', [])
                qualities = []
                seen = set()

                for f in formats:
                    height = f.get('height')
                    if height and height not in seen:
                        seen.add(height)
                        # Calculate size from tbr (total bitrate) if filesize is not available
                        if not f.get('filesize') and f.get('tbr'):
                            # tbr is in kbps, convert to bytes
                            size_bytes = (f['tbr'] * 1000 * info.get('duration', 0)) / 8
                        else:
                            size_bytes = f.get('filesize', 0)
                            
                        qualities.append({
                            'height': height,
                            'format_id': f['format_id'],
                            'ext': f.get('ext', 'unknown'),
                            'filesize': size_bytes,
                            'tbr': f.get('tbr', 0),
                            'duration': info.get('duration', 0)
                        })

                return sorted(qualities, key=lambda x: x['height'])
        except:
            return []

    @staticmethod
    def list_qualities(qualities: List[Dict]):
        """List available qualities."""
        if not qualities:
            print("No qualities available")
            return

        print("\nAvailable qualities:")
        for q in qualities:
            size_mb = q['filesize'] / (1024 * 1024) if q['filesize'] else 0
            print(f"{q['height']}p ({q['ext']}, {size_mb:.1f}MB)")

    @staticmethod
    def get_format_for_quality(url: str, target_quality: int) -> Optional[str]:
        """Get the format ID for the requested quality."""
        try:
            with yt_dlp.YoutubeDL({
                'quiet': True,
                'no_warnings': True,
                'nocheckcertificate': True,
                'ignoreerrors': True,
            }) as ydl:
                info = ydl.extract_info(url, download=False)
                if not info:
                    return None

                formats = info.get('formats', [])
                best_format = None
                min_diff = float('inf')

                for f in formats:
                    if f.get('height') == target_quality:
                        return f['format_id']
                    elif f.get('height'):
                        diff = abs(f['height'] - target_quality)
                        if diff < min_diff:
                            min_diff = diff
                            best_format = f['format_id']

                return best_format
        except:
            return None

    @staticmethod
    def compress_video(input_file: str, output_file: str, level: int) -> bool:
        """Compress video using FFmpeg with aggressive settings."""
        try:
            # Base CRF value (lower = higher quality)
            base_crf = 23
            
            # Additional compression parameters based on level
            if level == 0:
                return False  # No compression
            elif level == 1:
                crf = base_crf
                preset = 'medium'
                audio_bitrate = '192k'
            elif level == 2:
                crf = base_crf + 2
                preset = 'medium'
                audio_bitrate = '160k'
            elif level == 3:
                crf = base_crf + 4
                preset = 'medium'
                audio_bitrate = '128k'
            elif level == 4:
                crf = base_crf + 6
                preset = 'faster'
                audio_bitrate = '96k'
            elif level == 5:
                crf = base_crf + 8
                preset = 'faster'
                audio_bitrate = '64k'
            elif level == 6:
                crf = base_crf + 10
                preset = 'veryfast'
                audio_bitrate = '48k'
            elif level == 7:
                crf = base_crf + 12
                preset = 'veryfast'
                audio_bitrate = '32k'
            elif level == 8:
                crf = base_crf + 14
                preset = 'ultrafast'
                audio_bitrate = '24k'
            else:  # level 9
                crf = base_crf + 16
                preset = 'ultrafast'
                audio_bitrate = '16k'

            # Build FFmpeg command with aggressive settings
            command = [
                'ffmpeg', '-y',
                '-i', input_file,
                '-c:v', 'libx264',
                '-crf', str(crf),
                '-preset', preset,
                '-tune', 'film',  # Optimize for film content
                '-profile:v', 'baseline',  # Use baseline profile for better compatibility
                '-level', '3.0',  # Set compatibility level
                '-movflags', '+faststart',  # Enable fast start for web playback
                '-c:a', 'aac',
                '-b:a', audio_bitrate,
                '-ar', '44100',  # Standard audio sample rate
                '-ac', '2',  # Stereo audio
                '-filter:a', 'volume=1.5',  # Boost audio volume
                '-filter:v', f'scale=trunc(iw/2)*2:trunc(ih/2)*2',  # Ensure even dimensions
                output_file
            ]

            # Run FFmpeg
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            stdout, stderr = process.communicate()

            if process.returncode != 0:
                print(f"Error during compression: {stderr.decode()}")
                return False

            return True
        except Exception as e:
            print(f"Error compressing video: {str(e)}")
            return False 