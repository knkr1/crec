# crec

A simple command-line tool to download videos from various platforms.

## Features

- Download videos from multiple platforms:
  - YouTube
  - Twitter
  - Instagram
  - TikTok
  - And many more (experimental support for all other yt-dlp supported sites)
- Download audio only
- Download without audio
- Download thumbnails
- Compress videos
- Copy file path to clipboard
- List available qualities
- Custom filename patterns
- Playlist support
- Custom FFmpeg arguments

## Installation

```bash
pip install crec
```

## Usage

```bash
crec <url> [options]
```

### Options

- `-a, --audio-only`: Download audio only (same as -nvi)
- `-na, --no-audio`: Download without audio
- `-nvi, --no-video`: Download only audio
- `-nc, --no-copy`: Do not copy path to clipboard
- `-vi, --video`: Download video (default behavior)
- `-q, --quality`: Video quality (e.g., 720, 1080)
- `-ql, --quality-list`: List available qualities and their sizes
- `-co, --compress`: Compression level (0-9)
- `-o, --output-dir`: Output directory
- `-t, --thumbnail`: Download thumbnail
- `-f, --filename`: Custom filename pattern (e.g., "{title}\_{quality}")
- `-p, --playlist`: Download as playlist
- `-ff, --ffmpeg-args`: Custom FFmpeg arguments
- `-v, --version`: Show version information
- `-d, --delete`: Delete all contents in ~/crec
- `-op, --open`: Open ~/crec in file explorer

### Examples

```bash
# Download a video
crec https://youtube.com/watch?v=...

# Download audio only
crec https://youtube.com/watch?v=... -a

# Download without audio
crec https://youtube.com/watch?v=... -na

# Download specific quality
crec https://youtube.com/watch?v=... -q 720

# List available qualities
crec https://youtube.com/watch?v=... -ql

# Compress video
crec https://youtube.com/watch?v=... -co 5

# Download thumbnail
crec https://youtube.com/watch?v=... -t

# Custom filename
crec https://youtube.com/watch?v=... -f "{title}_{quality}"

# Download playlist
crec https://youtube.com/playlist?list=... -p

# Custom FFmpeg arguments
crec https://youtube.com/watch?v=... -ff "-c:v libx264 -crf 23"

# Delete all contents in ~/crec
crec -d

# Open ~/crec in file explorer
crec -op
```

## Filename Patterns

You can use the following placeholders in your filename pattern:

- `{title}`: Video title
- `{id}`: Video ID
- `{quality}`: Video quality
- `{date}`: Upload date

Example: `-f "{title}_{quality}"` will create a file like `My Video_720.mp4`

## Compression Levels

- `0`: No compression
- `1`: Light compression (high quality)
- `2-3`: Medium compression
- `4-5`: Moderate compression
- `6-7`: Heavy compression
- `8-9`: Extreme compression (low quality)

## Output Directories

By default, files are saved in:

- `~/crec/videos/` for videos
- `~/crec/audio/` for audio files
- `~/crec/photos/` for thumbnails

You can change the output directory with `-o`.

## Requirements

- Python 3.7+
- FFmpeg (for audio extraction and compression)

## License

MIT
