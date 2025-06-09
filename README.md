# 🎥 crec - The Ultimate Media Downloader

> One command to download any media. Downloads and copies the path to clipboard automatically! 🚀

## ✨ Features

- **One Command Magic**: Just paste the URL and get the file path in your clipboard! ✨
- **Smart Organization**: Files automatically sorted into videos, audio, photos folders 📁
- **Cross-Platform**: Works on Windows, macOS, and Linux 💫
- **Quality Control**: Choose your preferred quality or get the best available 🎯

- **One Command Magic**: Just paste the URL and watch the magic happen! ✨
- **Smart Organization**: Files automatically sorted into videos, audio, photos, and more! 📁
- **Cross-Platform**: Works on Windows, macOS, and Linux like a charm! (tbh, i haven't tested it lol) 💫
- **Clipboard Magic**: Downloaded file path automatically copied to clipboard! 📋

### 🎮 Basic Usage

```bash
# Install
pip install crec

# Download best quality
crec "https://youtube.com/..."

# Download audio only
crec -a "https://youtube.com/..."

# Download in 720p
crec -q 720 "https://youtube.com/..."
```

## 🛠️ Advanced Usage

```bash
# List available qualities
crec -ql "https://youtube.com/..."

# Download playlist
crec -p "https://youtube.com/playlist?list=..."

# Download with thumbnail
crec -t "https://youtube.com/..."

# Custom filename
crec -n "{title}_{quality}" "https://youtube.com/..."

# Custom output directory
crec -o "C:/Videos" "https://youtube.com/..."

# Open downloads folder
crec -op
```

## 🎯 Supported Platforms

- YouTube 🎥
- Twitter 🐦
- TikTok 📱
- Instagram 📸

## 💡 Pro Tips

1. Use `-ql` to check available qualities
2. Combine `-t` with `-n` for organized thumbnails
3. Use `-op` to quickly access your downloads
4. Custom FFmpeg args for pro-level compression: `--ffmpeg-args "-c:v libx264 -crf 23"`

---

Made with ❤️ for the internet community
