import os
from typing import Optional, List, Dict
from .youtube import YouTubeHandler
from .twitter import TwitterHandler
from .tiktok import TikTokHandler
from .instagram import InstagramHandler
from .generic import GenericHandler

class Handler:
    def __init__(self):
        self.handlers = {
            'youtube': YouTubeHandler(),
            'twitter': TwitterHandler(),
            'tiktok': TikTokHandler(),
            'instagram': InstagramHandler(),
            'generic': GenericHandler()
        }

    def get_handler(self, url: str):
        """Get the appropriate handler for the URL."""
        if 'youtube.com' in url or 'youtu.be' in url:
            return self.handlers['youtube']
        elif 'twitter.com' in url or 'x.com' in url:
            return self.handlers['twitter']
        elif 'tiktok.com' in url:
            return self.handlers['tiktok']
        elif 'instagram.com' in url:
            return self.handlers['instagram']
        else:
            return self.handlers['generic']

    def can_handle(self, url: str) -> bool:
        """Check if any handler can handle the URL."""
        handler = self.get_handler(url)
        return handler.can_handle(url)

    def download(self, url: str, audio_only: bool = False, quality: Optional[str] = None, 
                compress_level: int = 0, output_dir: Optional[str] = None, 
                download_thumbnail: bool = False, filename_pattern: Optional[str] = None,
                is_playlist: bool = False, ffmpeg_args: Optional[str] = None,
                no_audio: bool = False, copy_to_clipboard: bool = True) -> Optional[str]:
        """Download content from the URL using the appropriate handler."""
        handler = self.get_handler(url)
        if handler:
            return handler.download(
                url=url,
                audio_only=audio_only,
                quality=quality,
                compress_level=compress_level,
                output_dir=output_dir,
                download_thumbnail=download_thumbnail,
                filename_pattern=filename_pattern,
                is_playlist=is_playlist,
                ffmpeg_args=ffmpeg_args,
                no_audio=no_audio,
                copy_to_clipboard=copy_to_clipboard
            )
        return None 