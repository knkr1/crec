# 🎥 crec - The Ultimate Media Downloader

> One command to download any media. Downloads and copies the file to clipboard automatically! 🚀

## ✨ Features

- **One Command Magic**: Just paste the URL and get the file in your clipboard! ✨
- **Smart Organization**: Files automatically sorted into videos, audio, photos folders 📁
- **Cross-Platform**: Works on Windows, macOS, and Linux 💫
- **Quality Control**: Choose your preferred quality or get the best available 🎯

## 🚀 Quick Start

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

## 🛠️ Usage

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

## 📝 About

Built on top of [yt-dlp](https://github.com/yt-dlp/yt-dlp) for reliable downloads and enhanced with automatic file copying and organization features.

---

Made with ❤️ for the internet community
