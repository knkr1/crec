# crec installer for Windows.
#
#   irm https://raw.githubusercontent.com/knkr1/crec/main/install.ps1 | iex
#
# Installs crec to %LOCALAPPDATA%\Programs\crec and puts that folder on the user
# PATH. No admin rights, no Python, nothing else to install. Re-running it
# upgrades in place (crec 2.1+ can also just run `crec update`).
#
# Unattended: set $env:CREC_HOTKEYS = "1" (or "0") first to answer the hotkey
# question without being asked.
#
# Testing and portable installs:
#   $env:CREC_INSTALL_DIR   install somewhere else (PATH is then left to you)
#   $env:CREC_INSTALL_FROM  install a local crec-windows-x64.zip instead of a release
#
# Uninstall:  crec uninstall

$ErrorActionPreference = "Stop"

$Repo    = "knkr1/crec"
$Custom  = [bool]$env:CREC_INSTALL_DIR
$Dir     = if ($Custom) { $env:CREC_INSTALL_DIR.TrimEnd("\") } else { Join-Path $env:LOCALAPPDATA "Programs\crec" }
$Target  = Join-Path $Dir "crec.exe"
$Zip     = "crec-windows-x64.zip"

function Info($m)  { Write-Host "  $m" -ForegroundColor DarkGray }
function Good($m)  { Write-Host "  $m" -ForegroundColor Green }
function Warn($m)  { Write-Host "  $m" -ForegroundColor Yellow }

Write-Host ""
Write-Host "crec installer" -ForegroundColor Cyan
Write-Host ""

# --- work out what to download -------------------------------------------------
if ($env:CREC_INSTALL_FROM) {
    $local   = Get-Item $env:CREC_INSTALL_FROM
    $release = [pscustomobject]@{ tag_name = "(local build)"; assets = @() }
    $asset   = [pscustomobject]@{ name = $Zip; size = $local.Length; browser_download_url = $local.FullName }
    $legacy  = $null
} else {
    Info "Looking up the latest release..."
    try {
        $release = Invoke-RestMethod "https://api.github.com/repos/$Repo/releases/latest" `
            -Headers @{ "User-Agent" = "crec-installer" }
    } catch {
        throw "Could not reach GitHub to find the latest release. $($_.Exception.Message)"
    }
    $asset  = $release.assets | Where-Object { $_.name -eq $Zip } | Select-Object -First 1
    # Releases before 2.1 shipped a single crec.exe instead of a zipped folder.
    $legacy = $release.assets | Where-Object { $_.name -eq "crec.exe" } | Select-Object -First 1
}
$version = $release.tag_name
if (-not $asset -and -not $legacy) { throw "Release $version has nothing to install." }
$download = if ($asset) { $asset } else { $legacy }

$firstInstall = -not (Test-Path $Target)
if (-not $firstInstall) {
    $installed = (& $Target --version 2>$null) -join " "
    if (-not $env:CREC_INSTALL_FROM -and $installed -match [regex]::Escape($version.TrimStart("v"))) {
        Good "crec $version is already installed and current."
        Write-Host ""
        return
    }
    Info "Upgrading from: $installed"
}

# --- download and verify -------------------------------------------------------
$temp = Join-Path $env:TEMP "crec-$($version)-$($download.name)"
Info "Downloading crec $version ($([math]::Round($download.size / 1MB, 1)) MB)..."
try {
    $progress = $ProgressPreference
    $ProgressPreference = "SilentlyContinue"   # the built-in bar is very slow
    if ($env:CREC_INSTALL_FROM) { Copy-Item $download.browser_download_url $temp }
    else { Invoke-WebRequest -Uri $download.browser_download_url -OutFile $temp -UseBasicParsing }
    $ProgressPreference = $progress
} catch {
    throw "Download failed. $($_.Exception.Message)"
}

$sumAsset = $release.assets | Where-Object { $_.name -eq "$($download.name).sha256" } | Select-Object -First 1
if ($sumAsset) {
    try {
        # GitHub serves this as application/octet-stream, so -UseBasicParsing hands
        # back a byte[] rather than a string. Splitting that on whitespace yields
        # decimal byte values, not the hash - decode it explicitly.
        $body = (Invoke-WebRequest $sumAsset.browser_download_url -UseBasicParsing).Content
        if ($body -is [byte[]]) { $body = [Text.Encoding]::UTF8.GetString($body) }
        $expected = (([string]$body).Trim() -split '\s+' | Select-Object -First 1).Trim().ToUpper()
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

# --- stop a running tray, so its files can be replaced -------------------------
$trayWasRunning = [bool](Get-Process crecw -ErrorAction SilentlyContinue |
    Where-Object { $_.Path -and $_.Path.StartsWith($Dir, [StringComparison]::OrdinalIgnoreCase) })
if ($trayWasRunning) {
    Info "Stopping the crec tray for the upgrade..."
    & $Target tray --stop *> $null
    for ($i = 0; $i -lt 50 -and (Get-Process crecw -ErrorAction SilentlyContinue); $i++) {
        Start-Sleep -Milliseconds 100
    }
}

# --- put the files in place ----------------------------------------------------
# Build the new install beside the old one and swap by renaming: a rename either
# happens or it doesn't, so a failure can never leave a half-replaced crec.
$parent = Split-Path $Dir
New-Item -ItemType Directory -Force -Path $parent | Out-Null
$incoming = "$Dir.incoming"
$retired  = "$Dir.old"
Remove-Item $incoming, $retired -Recurse -Force -ErrorAction SilentlyContinue

if ($asset) {
    Expand-Archive -Path $temp -DestinationPath $incoming -Force
} else {
    New-Item -ItemType Directory -Force -Path $incoming | Out-Null
    Copy-Item $temp (Join-Path $incoming "crec.exe")
}
Remove-Item $temp -Force -ErrorAction SilentlyContinue
if (-not (Test-Path (Join-Path $incoming "crec.exe"))) {
    Remove-Item $incoming -Recurse -Force
    throw "The download did not contain crec.exe. Not installing."
}

if (Test-Path $Dir) {
    try {
        Move-Item $Dir $retired
    } catch {
        Remove-Item $incoming -Recurse -Force -ErrorAction SilentlyContinue
        throw "crec is still running (maybe in another terminal). Close it and run this again."
    }
}
Move-Item $incoming $Dir
Remove-Item $retired -Recurse -Force -ErrorAction SilentlyContinue
Good "Installed to $Dir"

# --- PATH ----------------------------------------------------------------------
# The whole point of this script: "put it on your PATH" is the step that stops
# people who do not already know what PATH is.
$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
$onPath = $Custom -or ($userPath -split ";" | ForEach-Object { $_.TrimEnd("\") }) -contains $Dir.TrimEnd("\")

if ($Custom) {
    Info "Custom install folder - add $Dir to your PATH yourself if you want it there."
    $needsNewTerminal = $false
} elseif (-not $onPath) {
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
if (-not $Custom -and $resolved -and ($resolved -ne $Target)) {
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

# --- hotkeys -------------------------------------------------------------------
$hasTray = Test-Path (Join-Path $Dir "crecw.exe")
$trayStarted = $false
if ($hasTray -and $trayWasRunning) {
    & $Target tray *> $null
    $trayStarted = $true
    Info "Restarted the crec tray."
} elseif ($hasTray -and $firstInstall) {
    $answer = $env:CREC_HOTKEYS
    if (-not $answer) {
        Write-Host ""
        Write-Host "  crec can run in the background with hotkeys: copy a link, press" -ForegroundColor White
        Write-Host "  Ctrl+Shift+Alt+C (or +D to shrink it for Discord), and paste." -ForegroundColor White
        try { $answer = Read-Host "  Turn on hotkeys and start them with Windows? [Y/n]" } catch { $answer = "n" }
    }
    if ($answer -notmatch '^(n|no|0|false)$') {
        & $Target tray --startup on *> $null
        & $Target tray *> $null
        $trayStarted = $true
        Good "Hotkeys are on - look for the crec icon by the clock."
    }
}

# --- done ----------------------------------------------------------------------
Write-Host ""
Good "crec $version is ready."
Write-Host ""
Write-Host "  Copy a video link, then run:" -ForegroundColor White
Write-Host "    crec" -ForegroundColor Cyan
if ($trayStarted) {
    Write-Host "  or just press Ctrl+Shift+Alt+C." -ForegroundColor White
}
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
