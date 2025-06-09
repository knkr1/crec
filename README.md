# 🎥 crec - The Ultimate Media Downloader

> Your one-stop solution for downloading videos, audio, and images from the web. No BS, just pure downloading power! 🚀

## ✨ Features

### 🎯 Core Features

- **One Command Magic**: Just paste the URL and watch the magic happen! ✨
- **Smart Organization**: Files automatically sorted into videos, audio, photos, and more! 📁
- **Cross-Platform**: Works on Windows, macOS, and Linux like a charm! 💫
- **Clipboard Magic**: Downloaded file path automatically copied to clipboard! 📋

### 🎮 Basic Usage

```bash
# Download best quality
crec "https://youtube.com/..."

# Download audio only
crec -a "https://youtube.com/..."

# Download in 720p
crec -q 720 "https://youtube.com/..."
```

### 🛠️ Advanced Features

#### 🎥 Video Options

```bash
# List available qualities
crec -ql "https://youtube.com/..."

# Compress video (light)
crec -f1 "https://youtube.com/..."

# Compress video (heavy)
crec -f2 "https://youtube.com/..."

# Custom FFmpeg settings
crec --ffmpeg-args "-c:v libx264 -crf 23" "https://youtube.com/..."
```

#### 📦 Playlist & Batch

```bash
# Download entire playlist
crec -p "https://youtube.com/playlist?list=..."

# Custom output directory
crec -o "C:/Videos" "https://youtube.com/..."
```

#### 🎨 Customization

```bash
# Download thumbnail
crec -t "https://youtube.com/..."

# Custom filename pattern
crec -n "{title}_{quality}" "https://youtube.com/..."
```

#### 🧹 Maintenance

```bash
# Open crec directory
crec -op

# Clean up downloads
crec -d
```

### 📝 Filename Patterns

Use these placeholders in your custom filenames:

- `{title}` - Video title
- `{id}` - Video ID
- `{quality}` - Video quality
- `{date}` - Upload date

Example: `crec -n "{title}_{quality}" "https://youtube.com/..."`

### 🎯 Supported Platforms

- YouTube 🎥
- Twitter 🐦
- TikTok 📱
- Instagram 📸

## 🚀 Installation

```bash
pip install crec
```

## 💡 Pro Tips

1. Use `-ql` to check available qualities before downloading
2. Combine `-t` with `-n` for organized thumbnails
3. Use `-op` to quickly access your downloads
4. Custom FFmpeg args for pro-level compression

## 🤝 Contributing

Found a bug? Want a new feature? PRs are welcome!

## 📜 License

MIT License - Feel free to use, modify, and distribute!

---

Made with ❤️ for the internet community
