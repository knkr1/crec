import os
import shutil
import sys
import argparse
from crec.sources.youtube import YouTubeHandler
from crec.sources.twitter import TwitterHandler
from crec.sources.tiktok import TikTokHandler
from crec.sources.instagram import InstagramHandler
from crec.utils.quality import QualityHandler
from crec.utils.notify import Notifier
from crec.utils.file_handler import FileHandler

def cleanup_ezds():
    """Delete all contents in the crec directory."""
    crec_dir = os.path.expanduser('~/crec')
    if os.path.exists(crec_dir):
        try:
            shutil.rmtree(crec_dir)
            os.makedirs(crec_dir)  # Recreate empty directory
            print(f"Successfully cleaned up {crec_dir}")
        except Exception as e:
            print(f"Error cleaning up directory: {str(e)}")
            sys.exit(1)
    else:
        print(f"Directory {crec_dir} does not exist")

def main():
    """Main entry point for the CLI."""
    parser = argparse.ArgumentParser(
        description='Quick video downloader for YouTube, Twitter, TikTok, and Instagram',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Examples:
  crec <url>                    Download best quality
  crec -a <url>                 Download audio only
  crec -q 360 <url>             Download 360p
  crec -ql <url>                Show available qualities
  crec -f1 <url>                Download with compression
  crec -f2 <url>                Download with heavy compression
  crec -d                       Clean up downloads folder
  crec -p <url>                 Download playlist
  crec -o "path/to/dir" <url>   Specify download directory
  crec -t <url>                 Download thumbnail
  crec -n "{title}_{quality}" <url>  Custom filename
  crec -op                      Open crec directory
  crec --ffmpeg-args "-c:v libx264 -crf 23" <url>  Custom FFmpeg options

Note: TikTok and Instagram support is experimental
'''
    )
    parser.add_argument('url', nargs='?', help='Video URL')
    parser.add_argument('-d', '--cleanup', action='store_true', help='Clean downloads folder')
    parser.add_argument('-a', '--audio', action='store_true', help='Audio only')
    parser.add_argument('-q', '--quality', help='Video quality (e.g., 360, 720)')
    parser.add_argument('-ql', '--list-qualities', action='store_true', help='Show available qualities')
    parser.add_argument('-f1', '--compress', action='store_true', help='Compress video')
    parser.add_argument('-f2', '--compress-heavy', action='store_true', help='Heavy compression')
    parser.add_argument('-p', '--playlist', action='store_true', help='Download playlist')
    parser.add_argument('-o', '--output', help='Output directory')
    parser.add_argument('-t', '--thumbnail', action='store_true', help='Download thumbnail')
    parser.add_argument('-n', '--name', help='Custom filename pattern (e.g., "{title}_{quality}")')
    parser.add_argument('-op', '--open-dir', action='store_true', help='Open crec directory')
    parser.add_argument('--ffmpeg-args', help='Custom FFmpeg arguments')
    
    args = parser.parse_args()
    
    if args.open_dir:
        FileHandler.open_crec_directory()
        if not args.url:
            return  # Exit after opening directory if no URL provided
    
    if args.cleanup:
        cleanup_ezds()
        if not args.url:
            return  # Exit after cleanup if no URL provided
    
    if not args.url:
        parser.print_help()
        sys.exit(1)

    # Handle quality listing
    if args.list_qualities:
        formats = QualityHandler.get_available_qualities(args.url)
        QualityHandler.list_qualities(formats)
        return

    # Determine compression level
    compress_level = 0
    if args.compress_heavy:
        compress_level = 2
    elif args.compress:
        compress_level = 1

    # Set up output directory
    output_dir = args.output if args.output else os.path.expanduser('~/crec/videos')
    os.makedirs(output_dir, exist_ok=True)

    # Try each handler in sequence
    handlers = [
        YouTubeHandler(),
        TwitterHandler(),
        TikTokHandler(),
        InstagramHandler()
    ]

    for handler in handlers:
        if handler.can_handle(args.url):
            result = handler.download(
                args.url,
                audio_only=args.audio,
                quality=args.quality,
                compress_level=compress_level,
                output_dir=output_dir,
                download_thumbnail=args.thumbnail,
                filename_pattern=args.name,
                is_playlist=args.playlist,
                ffmpeg_args=args.ffmpeg_args
            )
            if result:
                print(f"Downloaded: {result}")
                # Send notification
                Notifier.notify("Download Complete", f"Successfully downloaded: {os.path.basename(result)}")
            else:
                print("Download failed")
                Notifier.notify("Download Failed", "Failed to download video")
            return
    
    print("Error: Unsupported URL")
    sys.exit(1)

if __name__ == '__main__':
    main() 