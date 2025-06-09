# 🐊 crec

Your Swiss Army knife for downloading media from the internet. Just paste a URL and watch the magic happen!

## Features

- **Universal Downloader**: Download from multiple platforms:
  - YouTube
  - Twitter
  - Instagram
  - TikTok
  - And many more! (experimental support for all [yt-dlp supported sites](https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md))
- **Flexible Output Options**:
  - Audio-only downloads
  - Quality selection
  - Video compression
  - Custom output directories
  - Custom filename patterns
  - Thumbnail downloads
  - Playlist support
- **Advanced Features**:
  - Custom FFmpeg arguments
  - Progress tracking
  - Automatic file organization
  - Clipboard integration
- **Utility Commands**:
  - Open download directory
  - Clean up downloaded files

## Installation

```bash
pip install crec
```

## Usage

It's stupidly simple. Just paste a URL and add any options you want:

```bash
# Basic usage (just paste a URL)
crec https://youtube.com/watch?v=dQw4w9WgXcQ

# Want audio only? Add -a
crec https://youtube.com/watch?v=dQw4w9WgXcQ -a

# Want 720p? Add -q 720
crec https://youtube.com/watch?v=dQw4w9WgXcQ -q 720

# Want to compress it? Add -co 5
crec https://youtube.com/watch?v=dQw4w9WgXcQ -co 5
```

### All Options

```bash
# Video/Audio Options
-a, --audio-only      Download audio only
-na, --no-audio       Download without audio
-nvi, --no-video      Download only audio (same as -a)
-vi, --video          Download video (default behavior)

# Quality & Processing
-q, --quality         Video quality (e.g., 720, 1080)
-ql --quality-list    List video quailities
-co, --compress       Compression level (0-9)
-ff, --ffmpeg-args    Custom FFmpeg arguments

# Output Options
-o, --output-dir      Output directory
-t, --thumbnail       Download thumbnail
-f, --filename        Custom filename pattern
-p, --playlist        Download as playlist

# Utility Commands
-d, --delete          Delete all contents in ~/crec
-op, --open           Open ~/crec in file explorer
-v, --version         Show version information
-nc --no-copy         Will not copy the file to the clipboard
```

## Examples

```bash
# Download a video (it's that simple!)
crec https://youtube.com/watch?v=dQw4w9WgXcQ

# Get just the audio
crec https://youtube.com/watch?v=dQw4w9WgXcQ -a

# Get 720p quality
crec https://youtube.com/watch?v=dQw4w9WgXcQ -q 720

# Compress it a bit
crec https://youtube.com/watch?v=dQw4w9WgXcQ -co 5

# Save to a specific folder
crec https://youtube.com/watch?v=dQw4w9WgXcQ -o ~/Downloads

# Custom filename
crec https://youtube.com/watch?v=dQw4w9WgXcQ -f "{title}_{quality}"

# Download without audio
crec https://youtube.com/watch?v=dQw4w9WgXcQ -na

# Download and delete original after compression
crec https://youtube.com/watch?v=dQw4w9WgXcQ -co 5 -no

# Don't copy path to clipboard
crec https://youtube.com/watch?v=dQw4w9WgXcQ -nc

# Open download directory
crec -op

# Clean up downloaded files
crec -d
```

## Why crec?

- **Versatility**: Download from virtually any platform
- **Simplicity**: One command to rule them all
- **Power**: Advanced features when you need them
- **Reliability**: Built on yt-dlp
- **Flexibility**: Customize every aspect of your downloads

## License

MIT
