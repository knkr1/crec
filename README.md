# crec

Copy a link, type `crec`, paste the file wherever you were going to paste it.

```
crec
```

That's it for the normal case. crec reads the URL off your clipboard, downloads it into
`~/crec`, and then puts the finished file back on your clipboard, so the next thing you
do is Ctrl+V into Discord or a folder and you're done.

If you want more than that:

```bash
crec --discord              # shrink it until it fits Discord's limit
crec -c 0:10-0:40           # only that part
crec -c 0:10-0:40 -s 8      # only that part, and under 8 MB
crec -a                     # just the audio, reusing the last URL
crec --gif -c 1:00-1:05     # a five-second gif
```

---

## Contents

- [What this is for](#what-this-is-for)
- [Install](#install)
- [Flags](#flags)
- [Presets](#presets)
- [How the size targeting works](#how-the-size-targeting-works)
- [How trimming works](#how-trimming-works)
- [The clipboard](#the-clipboard)
- [Keeping yt-dlp current](#keeping-yt-dlp-current)
- [Commands](#commands)
- [Where things live](#where-things-live)
- [Development](#development)

---

## What this is for

yt-dlp already does the downloading, and ffmpeg does the encoding. crec is a wrapper,
and it's not shy about that. It exists because of three things those two won't do on
their own.

The main one is the clipboard. Getting a video into a Discord message normally means
downloading it, finding it in Explorer, then dragging it over. crec skips that whole
middle part.

The second is that you can ask for a result instead of a setting. `-s 10` means "make
this fit in 10 MB", which is a thing people actually want, whereas "compression level 5"
is a number nobody can reason about. crec works out the bitrate for you, and the section
below explains how, because it turned out to be more involved than I expected.

The third is trimming. `-c 0:10-0:40` and you get those thirty seconds, and on sites
that support it only those thirty seconds get downloaded in the first place.

Under the hood crec imports yt-dlp as a library rather than shelling out to it. That's
mostly so it can get real progress updates and the actual output filenames, instead of
parsing stdout and hoping.

---

## Install

On Windows, download `crec.exe` from the
[releases page](https://github.com/knkr1/crec/releases) and drop it somewhere on your
PATH. You don't need Python for that build.

The exe isn't code-signed, so the first time you run it Windows will show the
"Windows protected your PC" box. Click "More info", then "Run anyway". Getting rid of
that permanently needs a signing certificate that costs a couple hundred dollars a year,
which I'm not doing for this.

If you have Python 3.11 or newer, this works everywhere:

```bash
pipx install crec     # or pip install crec
```

You don't have to install ffmpeg. crec downloads it the first time it needs it and keeps
it in its own folder, and if you already have ffmpeg on your PATH it'll just use that
instead. `crec doctor` will tell you what it found.

It fetches ffmpeg rather than bundling it partly to keep the download small, and partly
because ffmpeg is GPL — as long as crec never ships a copy, that stays ffmpeg's
licensing situation and not crec's.

---

## Flags

The ones worth remembering:

| Flag | What it does |
|---|---|
| `-q 720` | cap the height |
| `-a` | audio only |
| `-c 0:10-0:40` | trim to a range |
| `-s 10` | make it fit under 10 MB |
| `-o DIR` | where to put it |
| `-n` | leave my clipboard alone |

The rest, for when you need them:

| Flag | What it does |
|---|---|
| `--format EXT` | `mp4` `mkv` `webm` `gif` `mp3` `m4a` `wav` |
| `--crf N` | target quality instead of size (18 is good, 28 is small) |
| `--scale W` | cap the width in pixels |
| `--fps N` | force a frame rate |
| `--mute` | throw away the audio |
| `--exact` | frame-accurate trim, which re-encodes and is slower |
| `-t` | save the thumbnail too |
| `-p` | grab the whole playlist |
| `--open` | show me the file when it's done |
| `--ffmpeg "..."` | pass your own arguments through to ffmpeg |
| `--last` | use the previous URL, ignore whatever's on the clipboard |

Timestamps for `-c` are flexible: `0:10-0:40`, `90-150` for plain seconds,
`1:02:03-1:02:10`, `0:10-` to go from there to the end, or `-0:40` to start at the
beginning.

---

## Presets

`--discord` isn't hardcoded anywhere. It's four lines of TOML, and so is everything like
it. This matters because Discord has already changed its upload limit more than once,
and I'd rather you could fix that yourself in ten seconds than wait for me to notice.

```bash
crec presets            # what's available
crec presets --edit     # open your own file
crec presets --keys     # what you're allowed to put in one
```

Your file lives at `%APPDATA%\crec\presets.toml` on Windows, or
`~/.config/crec/presets.toml` elsewhere:

```toml
[defaults]              # goes on every run
output_dir = "D:/clips"

[clip]                  # now you can type: crec --clip
description = "720p, under 8 MB, no sound"
quality = 720
target_mb = 8
no_audio = true
```

The ones that ship with it are `--discord`, `--discord-nitro`, `--whatsapp`,
`--telegram`, `--gif`, `--mp3`, `--music`, `--small` and `--hd`. If you define a preset
with the same name as one of those, yours wins.

Ordering goes `[defaults]` first, then any presets you named in the order you named
them, then anything you typed explicitly. So `crec --discord -s 8` gives you 8 MB, and
you can stack them like `crec --small --discord`.

Internally there are only about eighteen things the engine actually understands
(`crec presets --keys` lists them), and a preset is just a named bundle of those. That's
why most new ideas end up being TOML rather than code. If you typo a key name, crec
drops that one preset and tells you, rather than refusing to start, and if you pick a
name that collides with a real flag it says so — that check runs against the actual
parser, so it can't fall out of sync.

---

## How the size targeting works

This is the part I got wrong the first time, so it's worth writing down properly.
The short version: crec asked for 10 MB and produced 11.5 MB, which for a hard upload
limit is the same as producing nothing.

### Subtracting what the container costs

An MP4 isn't only video and audio. Every sample in the file gets an entry in the sample
tables (`stsz`, `stco`, `stsc`, `ctts`), and none of that space is available for
picture.

Originally crec reserved a flat 3% for this, which is wrong in a way that only shows up
on long files: the overhead scales with how many frames there are, not with how big the
file is, so 60fps costs about twice what 30fps costs for the same duration. Pulling
apart a real 634-second encode:

```
file total :  11,850,291 B  (11.30 MB)
video      :   6,346,266 B  (6.05 MB)   19,037 packets
audio      :   4,830,749 B  (4.61 MB)   29,749 packets
container  :     673,276 B  (0.64 MB)   = 5.7% of the file
```

673,276 bytes spread over 48,786 packets works out to about 13.8 bytes each. crec now
reserves 15 bytes per sample plus 64 KB for the `moov` atom:

```
audio_samples = duration × sample_rate / 1024      (an AAC frame is 1024 samples)
video_samples = duration × fps
overhead      = (video_samples + audio_samples) × 15 B + 64 KB
```

On that same file the model reserves 779 KB where 657 KB was actually used. That's about
18% more than needed and I left it that way, because reserving too little means the file
blows past the limit and is useless, while reserving too much costs a bit of bitrate
nobody will ever notice.

### Reserving what the audio encoder actually produces

ffmpeg's built-in AAC encoder does not really honour `-b:a`. Measured across the same
634 seconds:

| asked for | got | difference |
|---|---|---|
| 48k stereo | 62.4 kbps | +30.0% |
| 48k mono | 49.9 kbps | +4.0% |
| 64k mono | 66.0 kbps | +3.1% |
| 96k stereo | 98.1 kbps | +2.2% |
| 128k stereo | 129.9 kbps | +1.5% |

At 48 kbps in stereo it simply can't do it, and that 30% was most of the original
overshoot. Two things came out of that. crec now budgets audio at 1.06× whatever it
asked for, and at 64 kbps or below it encodes in mono, which sounds better than stereo
at that bitrate anyway and actually hits the number.

When space is tight the audio steps down through 128, 96, 64 and 48 kbps, stopping at
the first one that still leaves enough for watchable video.

### Working out the video bitrate

```
payload    = target_bytes − overhead
budget     = payload × 8 / duration / 1000          (kbps)
video_kbps = budget − (audio_kbps × 1.06)
```

### Picking a resolution that suits the bitrate

1080p at 300 kbps looks like mud, but 480p at 300 kbps is perfectly watchable, so crec
drops the resolution to match the budget instead of fighting it:

| height | wants at least |
|---|---|
| 2160p | 12000 kbps |
| 1440p | 6000 kbps |
| 1080p | 2500 kbps |
| 720p | 1200 kbps |
| 480p | 600 kbps |
| 360p | 350 kbps |
| 240p | 180 kbps |
| 144p | 90 kbps |

### Not downloading pixels it's about to throw away

Since crec can work all of this out before downloading anything, it can also cap what it
downloads. If you're targeting 10 MB on a longish video the answer is going to be around
360p, so there's no reason to pull the 4K stream first — it grabs 480p instead and the
result is identical.

It deliberately caps one step above the height it's aiming for. Downscaling 480p to 360p
looks better than encoding a native 360p source, and at these sizes the extra bandwidth
is nothing.

This needs a metadata lookup before the download starts. If the site won't say how long
the video is, which happens with live streams and a few extractors, crec skips the cap
rather than guessing at it.

### Saying no quickly

Some targets just aren't achievable. crec won't go below 80 kbps of video, because past
that you're not really watching anything. Since it checks before downloading, you find
out in about two seconds instead of after several hundred megabytes:

```
$ crec --discord            # on a 10:35 video
crec: 10 MB cannot hold 10m35s of video. Try at least 11 MB,
      or trim it with -c (about 1m59s would fit).
```

I made that an error rather than a warning. The alternative is a five-minute encode that
produces something unwatchable and still misses the limit, which helps nobody.

### Checking afterwards

Encoders miss their targets, so crec measures what came out. If it's over, it re-encodes
once with the budget scaled down by however far off it was (`limit / actual × 0.98`). If
that correction would push the video below the floor it stops and says so, rather than
spending another few minutes producing a second file that's also too big.

It uses two-pass x264 throughout. Single-pass `-b:v` overshoots, and overshooting is the
one failure mode that makes the whole file pointless.

### Does it actually work

| what | limit | result |
|---|---|---|
| 10 second clip | 2 MB | 1.9731 MB |
| 15 second cut | 10 MB | 9.9627 MB |
| 2 minute cut from a 4K source | 10 MB | 9.7628 MB, and it downloaded 480p rather than 2160p |
| 10 minute video | 10 MB | refused after 2.3 seconds |

---

## How trimming works

`-c 0:10-0:40` gets handed to yt-dlp as a download range, so on sites that support it
only that section comes down the wire. On a long video that's the difference between
seconds and minutes.

Not every extractor honours it though. Rather than keeping a list of which ones do,
crec checks the result: it knows how long the source was supposed to be, so if the file
comes back longer than the range implies it trims locally and mentions that the site
ignored the request. There's a 10% tolerance in there so keyframe alignment doesn't
trigger it. If the site didn't report a duration at all, crec says it couldn't verify
rather than quietly assuming everything went fine.

By default the cut lands on the nearest keyframe, because that path just seeks and
copies the stream, which is lossless and basically instant. `--exact` re-encodes to get
the exact frame you asked for, which is correct but slow, so you have to ask for it.

One note for anyone reading the ffmpeg calls: on the fast path the seek options go
before `-i`, and there `-to` is an absolute position on the source timeline while `-t`
is a duration measured from the seek point. If you use `-to` with `end - start` you get
a clip half as long as you asked for, silently. crec uses `-t`, and there's a test
pinning that so it doesn't come back.

---

## The clipboard

Putting an actual file on the clipboard works on Windows through `CF_HDROP`, written
with raw ctypes, and on macOS through `NSPasteboard` driven by `osascript` so there's no
extra dependency. In both cases the system copies the data, so it survives crec exiting.

It doesn't work on Linux, and that's not laziness on my part. On X11 and Wayland the
clipboard contents belong to the process that put them there and vanish when it exits,
so a command that downloads something and then quits genuinely cannot leave a file on
the clipboard without hanging around as a daemon. On Linux crec prints the path and
explains why.

On Windows it only sets `CF_HDROP`, not text as well. If both are present a lot of apps
prefer the text, and you'd end up pasting a file path into Discord instead of uploading
the video.

One side effect worth knowing about: since crec replaces the URL on your clipboard with
the finished file, running it again immediately afterwards would find nothing. So it
remembers the last URL, which means `crec` followed by `crec -a` gets you the audio of
the same video without going back to the browser.

Everything else — downloading, trimming, size targeting, gifs — works the same on all
three platforms.

---

## Keeping yt-dlp current

Sites break constantly and yt-dlp ships fixes several times a month. If yt-dlp were
baked into `crec.exe`, every one of those fixes would need a new crec release, and
anyone whose download broke would be stuck waiting on me.

So crec keeps its own copy in `%LOCALAPPDATA%\crec\lib` and puts that at the front of
`sys.path` before importing, which means it wins over whatever's bundled. This works
because yt-dlp is pure Python, and it holds up inside a PyInstaller onefile build too —
I checked, because the whole design depends on it. The exe still ships a bundled copy so
a fresh install works before it's ever been online.

When a download fails in a way that looks like a stale extractor, crec updates yt-dlp and
retries once by itself, since that's the moment it actually matters. Otherwise it checks
once a day, after your download has finished so it never adds latency to anything. Or
you can just run `crec update`.

---

## Commands

```bash
crec update                 # update yt-dlp now
crec doctor                 # what's installed and where
crec doctor --fix           # install whatever's missing
crec presets                # list every preset
crec presets --edit         # open your presets file
crec presets --keys         # everything a preset can set
```

---

## Where things live

| | Windows | macOS and Linux |
|---|---|---|
| Downloads | `~\crec` | `~/crec` |
| Your presets | `%APPDATA%\crec\presets.toml` | `~/.config/crec/presets.toml` |
| ffmpeg, yt-dlp, state | `%LOCALAPPDATA%\crec\` | `~/.local/share/crec/` |

To change the download folder for good, set `output_dir` under `[defaults]` in your
presets file. For one run, use `-o`.

---

## Development

```bash
git clone https://github.com/knkr1/crec
cd crec
pip install -e . pytest
pytest tests -q
```

The tests don't touch the network and don't need ffmpeg, so they run anywhere. There are
69 of them and they cover the parts that make decisions — the size arithmetic, preset
precedence, timestamp parsing, the range verification, and the exact ffmpeg arguments —
because that's where every bug so far has actually been.

To build the standalone exe:

```bash
pip install pyinstaller
pyinstaller crec.spec --noconfirm    # ends up in dist/crec.exe
```

Roughly what's where:

| file | what's in it |
|---|---|
| `cli.py` | arguments, preset resolution, and the pipeline after a download |
| `options.py` | the eighteen primitives everything else reduces to |
| `presets.py` | loading and merging the TOML |
| `download.py` | yt-dlp as a library, the metadata peek, the stale-extractor retry |
| `media.py` | ffmpeg: probing, trimming, size targeting, gifs |
| `clipboard.py` | `CF_HDROP` and `NSPasteboard` |
| `deps.py` | fetching and updating yt-dlp and ffmpeg |

---

## Credit

[yt-dlp](https://github.com/yt-dlp/yt-dlp) does the downloading and
[ffmpeg](https://ffmpeg.org/) does the encoding. crec is the convenient bit on top of
two projects that are doing all the hard work.

## License

MIT.
