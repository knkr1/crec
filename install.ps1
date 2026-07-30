# crec installer for Windows.
#
#   irm https://raw.githubusercontent.com/knkr1/crec/main/install.ps1 | iex
#
# Installs crec.exe to %LOCALAPPDATA%\Programs\crec and puts that folder on the
# user PATH. No admin rights, no Python, nothing else to install. Re-running it
# upgrades in place.
#
# Uninstall:  Remove-Item "$env:LOCALAPPDATA\Programs\crec" -Recurse
#             (and drop the folder from PATH in the environment variables dialog)

$ErrorActionPreference = "Stop"

$Repo    = "knkr1/crec"
$Dir     = Join-Path $env:LOCALAPPDATA "Programs\crec"
$Target  = Join-Path $Dir "crec.exe"

function Info($m)  { Write-Host "  $m" -ForegroundColor DarkGray }
function Good($m)  { Write-Host "  $m" -ForegroundColor Green }
function Warn($m)  { Write-Host "  $m" -ForegroundColor Yellow }

Write-Host ""
Write-Host "crec installer" -ForegroundColor Cyan
Write-Host ""

# --- work out what to download -------------------------------------------------
Info "Looking up the latest release..."
try {
    $release = Invoke-RestMethod "https://api.github.com/repos/$Repo/releases/latest" `
        -Headers @{ "User-Agent" = "crec-installer" }
} catch {
    throw "Could not reach GitHub to find the latest release. $($_.Exception.Message)"
}

$version = $release.tag_name
$asset   = $release.assets | Where-Object { $_.name -eq "crec.exe" } | Select-Object -First 1
if (-not $asset) { throw "Release $version has no crec.exe attached." }

# Already up to date?
if (Test-Path $Target) {
    $installed = (& $Target --version 2>$null) -join " "
    if ($installed -match [regex]::Escape($version.TrimStart("v"))) {
        Good "crec $version is already installed and current."
        Write-Host ""
        return
    }
    Info "Upgrading from: $installed"
}

# --- download ------------------------------------------------------------------
New-Item -ItemType Directory -Force -Path $Dir | Out-Null
$temp = Join-Path $Dir "crec.exe.download"

Info "Downloading crec $version ($([math]::Round($asset.size / 1MB, 1)) MB)..."
try {
    $progress = $ProgressPreference
    $ProgressPreference = "SilentlyContinue"   # the built-in bar is very slow
    Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $temp -UseBasicParsing
    $ProgressPreference = $progress
} catch {
    throw "Download failed. $($_.Exception.Message)"
}

# --- verify the checksum, if the release published one -------------------------
$sumAsset = $release.assets | Where-Object { $_.name -eq "crec.exe.sha256" } | Select-Object -First 1
if ($sumAsset) {
    try {
        # GitHub serves this as application/octet-stream, so -UseBasicParsing hands
        # back a byte[] rather than a string. Splitting that on whitespace yields
        # decimal byte values, not the hash - decode it explicitly.
        $body = (Invoke-WebRequest $sumAsset.browser_download_url -UseBasicParsing).Content
        if ($body -is [byte[]]) { $body = [Text.Encoding]::UTF8.GetString($body) }
        $expected = ([string]$body).Trim() -split '\s+' | Select-Object -First 1
        $expected = $expected.Trim().ToUpper()
        $actual = (Get-FileHash $temp -Algorithm SHA256).Hash.ToUpper()
        if ($expected -and $actual -ne $expected) {
            Remove-Item $temp -Force
            throw "Checksum mismatch. Expected $expected, got $actual. Not installing."
        }
        Info "Checksum verified."
    } catch {
        if ($_.Exception.Message -like "Checksum mismatch*") { throw }
        Warn "Could not verify the checksum, continuing anyway."
    }
}

# The running exe cannot be overwritten, so swap it out.
if (Test-Path $Target) { Remove-Item $Target -Force -ErrorAction SilentlyContinue }
Move-Item $temp $Target -Force
Good "Installed to $Target"

# --- PATH ----------------------------------------------------------------------
# The whole point of this script: "put it on your PATH" is the step that stops
# people who do not already know what PATH is.
$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
$onPath = ($userPath -split ";" | ForEach-Object { $_.TrimEnd("\") }) -contains $Dir.TrimEnd("\")

if (-not $onPath) {
    $updated = if ([string]::IsNullOrWhiteSpace($userPath)) { $Dir } else { "$($userPath.TrimEnd(';'));$Dir" }
    [Environment]::SetEnvironmentVariable("Path", $updated, "User")
    Good "Added to your PATH."
    $needsNewTerminal = $true
} else {
    Info "Already on your PATH."
    $needsNewTerminal = $false
}

# Make it work in this session too.
if ($env:Path -notlike "*$Dir*") { $env:Path = "$env:Path;$Dir" }

# --- is anything else shadowing us? --------------------------------------------
# Anyone upgrading from a pip-installed crec has one on PATH already, usually
# ahead of ours. Without this check they would run the installer, see it succeed,
# and still be using the old version with no idea why.
$resolved = (Get-Command crec -ErrorAction SilentlyContinue).Source
if ($resolved -and ($resolved -ne $Target)) {
    Write-Host ""
    Warn "Heads up: another crec is earlier on your PATH and will win:"
    Warn "    $resolved"
    if ($resolved -match "[\\/]Scripts[\\/]crec\.exe$") {
        Warn "  That looks like a pip install. To use this one instead, run:"
        Warn "    pip uninstall crec"
    } else {
        Warn "  Remove it, or move $Dir earlier in your PATH."
    }
}

# --- done ----------------------------------------------------------------------
Write-Host ""
Good "crec $version is ready."
Write-Host ""
Write-Host "  Copy a video link, then run:" -ForegroundColor White
Write-Host "    crec" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Other things to try:" -ForegroundColor White
Write-Host "    crec --discord            shrink it to fit Discord" -ForegroundColor DarkGray
Write-Host "    crec -c 0:10-0:40         just that part" -ForegroundColor DarkGray
Write-Host "    crec -a                   audio only" -ForegroundColor DarkGray
Write-Host "    crec --help               everything else" -ForegroundColor DarkGray
Write-Host ""
if ($needsNewTerminal) {
    Warn "Open a new terminal first - PATH changes do not reach windows that are already open."
    Write-Host ""
}
