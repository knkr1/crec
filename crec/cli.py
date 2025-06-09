import os
import shutil
import sys
import argparse
import importlib.metadata
import subprocess
from crec.sources.youtube import YouTubeHandler
from crec.sources.twitter import TwitterHandler
from crec.sources.tiktok import TikTokHandler
from crec.sources.instagram import InstagramHandler
from crec.utils.quality import QualityHandler
from crec.utils.notify import Notifier
from crec.utils.file_handler import FileHandler
from typing import Optional, List, Dict
from .sources.handler import Handler
from .utils.version import check_version

def get_version():
    try:
        return importlib.metadata.version("crec")
    except importlib.metadata.PackageNotFoundError:
        return "unknown"

def delete_crec_contents():
    """Delete all contents in the crec directory."""
    crec_dir = os.path.expanduser('~/crec')
    if os.path.exists(crec_dir):
        for item in os.listdir(crec_dir):
            item_path = os.path.join(crec_dir, item)
            if os.path.isfile(item_path):
                os.remove(item_path)
            elif os.path.isdir(item_path):
                shutil.rmtree(item_path)
        print("All contents in ~/crec have been deleted.")
    else:
        print("~/crec directory does not exist.")

def open_crec_explorer():
    """Open file explorer at the crec directory."""
    crec_dir = os.path.expanduser('~/crec')
    if not os.path.exists(crec_dir):
        os.makedirs(crec_dir)
    
    if sys.platform == 'win32':
        os.startfile(crec_dir)
    elif sys.platform == 'darwin':  # macOS
        subprocess.run(['open', crec_dir])
    else:  # Linux
        subprocess.run(['xdg-open', crec_dir])
    print(f"Opened {crec_dir} in file explorer.")

def parse_args():
    parser = argparse.ArgumentParser(description='Download videos from various platforms')
    
    # URL argument (positional)
    parser.add_argument('url', nargs='?', help='URL of the video to download')
    
    # Special argument combinations
    parser.add_argument('-na', '--no-audio', action='store_true', help='Download without audio')
    parser.add_argument('-nvi', '--no-video', action='store_true', help='Download only audio')
    parser.add_argument('-nc', '--no-copy', action='store_true', help='Do not copy path to clipboard')
    parser.add_argument('-vi', '--video', action='store_true', help='Download video (default behavior)')
    
    # Regular arguments
    parser.add_argument('-a', '--audio-only', action='store_true', help='Download audio only (same as -nvi)')
    parser.add_argument('-q', '--quality', help='Video quality (e.g., 720, 1080)')
    parser.add_argument('-ql', '--quality-list', action='store_true', help='List available qualities and their sizes')
    parser.add_argument('-co', '--compress', type=int, default=0, help='Compression level (0-9)')
    parser.add_argument('-o', '--output-dir', help='Output directory')
    parser.add_argument('-t', '--thumbnail', action='store_true', help='Download thumbnail')
    parser.add_argument('-f', '--filename', help='Custom filename pattern (e.g., "{title}_{quality}")')
    parser.add_argument('-p', '--playlist', action='store_true', help='Download as playlist')
    parser.add_argument('-ff', '--ffmpeg-args', help='Custom FFmpeg arguments')
    parser.add_argument('-v', '--version', action='store_true', help='Show version information')
    
    # Utility commands
    parser.add_argument('-d', '--delete', action='store_true', help='Delete all contents in ~/crec')
    parser.add_argument('-op', '--open', action='store_true', help='Open ~/crec in file explorer')
    
    args = parser.parse_args()
    
    # Handle utility commands first
    if args.delete:
        delete_crec_contents()
        sys.exit(0)
    
    if args.open:
        open_crec_explorer()
        sys.exit(0)
    
    # Handle special combinations
    if args.no_audio and args.no_video:
        print("Error: Cannot use both -na and -nvi")
        sys.exit(1)
    
    # Set audio_only based on -nvi or -a
    if args.no_video or args.audio_only:
        args.audio_only = True
    
    return args

def show_version():
    version = get_version()
    print(f"crec v{version}")
    print("\nSupported platforms:")
    print("- YouTube")
    print("- Twitter")
    print("- Instagram")
    print("- TikTok")
    print("- And many more (experimental support for all other yt-dlp supported sites)")
    sys.exit(0)

def main():
    """Main entry point for the CLI."""
    args = parse_args()
    
    if args.version:
        show_version()
    
    # Check for updates
    has_update, current, latest = check_version()
    if has_update:
        print(f"\n✨ A new version of crec is available!")
        print(f"Current version: {current}")
        print(f"Latest version: {latest}")
        print("Update with: pip install --upgrade crec\n")
    
    if not args.url:
        print("Error: URL is required")
        print("\nUsage: crec <url> [options]")
        print("\nExample: crec https://youtube.com/watch?v=... -q 720")
        sys.exit(1)

    # Handle quality list
    if args.quality_list:
        qualities = QualityHandler.get_available_qualities(args.url)
        if qualities:
            print("\nAvailable qualities:")
            print("─" * 30)
            print(f"{'Quality':<10} {'Size':<10}")
            print("─" * 30)
            has_zero_size = False
            for q in qualities:
                size_mb = q['filesize'] / (1024 * 1024) if q['filesize'] else 0
                if size_mb == 0:
                    has_zero_size = True
                format_str = f" [{q['ext']}]" if q['ext'] != 'mp4' else ""
                print(f"{q['height']}p{format_str:<5} {size_mb:.1f}MB")
            print("─" * 30)
            if has_zero_size:
                print("\n⚠️  Note: Some qualities show 0.0MB because the website doesn't provide size information.")
                print("The actual file size will be available after downloading.")
            print("\nUse -q <quality> to download specific quality")
        else:
            print("Error: Could not get quality information")
        sys.exit(0)

    handler = Handler()
    if not handler.can_handle(args.url):
        print("Error: Unsupported URL")
        sys.exit(1)

    result = handler.download(
        url=args.url,
        audio_only=args.audio_only,
        quality=args.quality,
        compress_level=args.compress,
        output_dir=args.output_dir,
        download_thumbnail=args.thumbnail,
        filename_pattern=args.filename,
        is_playlist=args.playlist,
        ffmpeg_args=args.ffmpeg_args,
        no_audio=args.no_audio,
        copy_to_clipboard=not args.no_copy
    )

    if result:
        print(f"\nDownload completed: {result}")
    else:
        print("\nDownload failed")
        sys.exit(1)

if __name__ == "__main__":
    main() 