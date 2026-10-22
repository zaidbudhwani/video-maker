# Sets up the Video Maker on a Windows 10/11 computer. Run it by double-clicking "Install Video Maker.bat".
#
# Safe to run again: it skips what is already there and never overwrites .env, accounts or videos.
# It installs Python 3.11, ffmpeg and cloudflared (through winget), the Python packages at the exact
# versions tested, and the three model/tool files that are too big for git.
#
#   -Unattended   no questions, no Notepad, no account prompt (used by the install test)
param([switch]$Unattended)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root
$failed = @()

function Step($n, $text) { Write-Host ""; Write-Host "[$n/8] $text" -ForegroundColor Cyan }
function Ok($text)   { Write-Host "      OK: $text" -ForegroundColor Green }
function Note($text) { Write-Host "      $text" -ForegroundColor Yellow }

# A file that is too big for git: fetch it once from its official home.
function Get-Big($url, $path, $minBytes, $what) {
    if ((Test-Path $path) -and ((Get-Item $path).Length -ge $minBytes)) { Ok "$what is already here"; return }
    New-Item -ItemType Directory -Force (Split-Path -Parent $path) | Out-Null
    Write-Host "      downloading $what..."
    try {
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        Invoke-WebRequest -Uri $url -OutFile "$path.part" -UseBasicParsing
        if ((Get-Item "$path.part").Length -lt $minBytes) { throw "the download came back too small" }
        Move-Item -Force "$path.part" $path
        Ok $what
    } catch {
        Remove-Item -Force "$path.part" -ErrorAction SilentlyContinue
        Note "Could not download $what ($($_.Exception.Message)). Videos still work without it."
        $script:failed += $what
    }
}

function Find-Python {
    foreach ($c in @("$env:LOCALAPPDATA\Programs\Python\Python311\python.exe", "C:\Program Files\Python311\python.exe", "C:\Python311\python.exe")) {
        if (Test-Path $c) { return $c }
    }
    try { $p = (& py -3.11 -c "import sys; print(sys.executable)" 2>$null); if ($p -and (Test-Path $p)) { return $p } } catch {}
    return $null
}

function Winget-Install($id, $what) {
    Write-Host "      installing $what (this can take a few minutes)..."
    & winget install -e --id $id --accept-source-agreements --accept-package-agreements --silent | Out-Null
    # winget returns non-zero for "already installed"; what matters is whether the program is there afterwards
}

Write-Host "Setting up the Video Maker in $root" -ForegroundColor White

# ---------- 1. winget ----------
Step 1 "Checking this computer"
if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
    Write-Host "This computer doesn't have 'winget' (the Windows app installer)." -ForegroundColor Red
    Write-Host "Open the Microsoft Store, install 'App Installer', then run this again." -ForegroundColor Red
    exit 1
}
$os = [Environment]::OSVersion.Version
$ramGB = [math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB)
$freeGB = [math]::Round((Get-PSDrive ($root.Substring(0,1))).Free / 1GB)
Ok "Windows $($os.Major).$($os.Build), $ramGB GB memory, $freeGB GB free disk"
if ($ramGB -lt 8)   { Note "Under 8 GB of memory: videos will be slow, and long talking videos may fail." }
if ($freeGB -lt 10) { Note "Under 10 GB free: the install needs about 5 GB and every video keeps its working files." }

# ---------- 2. Python ----------
Step 2 "Python 3.11"
$py = Find-Python
if (-not $py) { Winget-Install "Python.Python.3.11" "Python 3.11"; $py = Find-Python }
if (-not $py) { Write-Host "Python 3.11 didn't install. Install it from python.org (3.11.x, 64-bit), then run this again." -ForegroundColor Red; exit 1 }
Ok $py

# ---------- 3. ffmpeg + cloudflared ----------
Step 3 "ffmpeg (video engine) and cloudflared (the shareable link)"
$ff = Get-ChildItem "$env:LOCALAPPDATA\Microsoft\WinGet\Packages\Gyan.FFmpeg*\*\bin\ffmpeg.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $ff -and -not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
    Winget-Install "Gyan.FFmpeg" "ffmpeg"
    $ff = Get-ChildItem "$env:LOCALAPPDATA\Microsoft\WinGet\Packages\Gyan.FFmpeg*\*\bin\ffmpeg.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
}
if ($ff -or (Get-Command ffmpeg -ErrorAction SilentlyContinue)) { Ok "ffmpeg" } else { Note "ffmpeg didn't install. Nothing works without it: run 'winget install Gyan.FFmpeg'."; $failed += "ffmpeg" }
$cf = "C:\Program Files (x86)\cloudflared\cloudflared.exe"
if (-not (Test-Path $cf) -and -not (Get-Command cloudflared -ErrorAction SilentlyContinue)) { Winget-Install "Cloudflare.cloudflared" "cloudflared" }
if ((Test-Path $cf) -or (Get-Command cloudflared -ErrorAction SilentlyContinue)) { Ok "cloudflared" } else { Note "cloudflared didn't install. Only needed for using it from a phone (Start Website.bat)." }

# ---------- 4. Python packages ----------
Step 4 "Python packages (the big download: about 1 GB)"
$venvPy = Join-Path $root "venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) { & $py -m venv (Join-Path $root "venv"); if (-not (Test-Path $venvPy)) { Write-Host "Couldn't create the venv folder." -ForegroundColor Red; exit 1 } }
& $venvPy -m pip install --quiet --upgrade pip 2>$null
# --no-deps: the lock file already lists every package at its tested version. Letting pip pick
# dependencies would pull in opencv-python, which breaks the opencv that mediapipe needs.
& $venvPy -m pip install --quiet --no-deps -r requirements.lock.txt
if ($LASTEXITCODE -ne 0) { Write-Host "Package install failed. Check the internet connection and run this again." -ForegroundColor Red; exit 1 }
& $venvPy -m pip install --quiet --no-deps -r requirements-nodeps.txt
Ok "packages installed at the tested versions"

# ---------- 5. Files too big for git ----------
Step 5 "Model and tool files"
Get-Big "https://storage.googleapis.com/mediapipe-models/face_detector/blaze_face_short_range/float16/latest/blaze_face_short_range.tflite" `
        (Join-Path $root "assets\models\blaze_face_short_range.tflite") 100000 "face-detection model (keeps her face in frame)"
Get-Big "https://github.com/Rikorose/DeepFilterNet/releases/download/v0.5.6/deep-filter-0.5.6-x86_64-pc-windows-msvc.exe" `
        (Join-Path $root "assets\tools\deep-filter.exe") 10000000 "noise remover (optional)"
New-Item -ItemType Directory -Force (Join-Path $root "assets\music"), (Join-Path $root "out"), (Join-Path $root "samples") | Out-Null

# ---------- 6. Speech model ----------
Step 6 "Speech-to-text model (about 500 MB, one time)"
& $venvPy -W ignore -c "from faster_whisper import WhisperModel; WhisperModel('small', device='cpu', compute_type='int8'); print('      OK: speech model ready')"
if ($LASTEXITCODE -ne 0) { Note "The speech model didn't download. It will try again the first time a video is made."; $failed += "speech model" }

# ---------- 7. Settings and account ----------
Step 7 "Settings and your sign-in"
if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Ok "made the settings file (.env)"
    if (-not $Unattended) {
        Write-Host ""
        Write-Host "      Notepad will open the settings file. Two lines to fill in, then save and close it:" -ForegroundColor White
        Write-Host "        ANTHROPIC_API_KEY=   your key from console.anthropic.com (for AI titles and clip matching)"
        Write-Host "        HELP_CONTACT=        who to contact, e.g.  Text the maintainer at 319-555-0100"
        Write-Host "      Without a key the tool still makes videos, with plainer titles."
        Start-Process notepad ".env" -Wait
    }
} else { Ok "settings file (.env) already exists, left as it is" }

$users = & $venvPy -W ignore manage_users.py list 2>$null
if ($users) { Ok "website accounts already exist: $(($users | ForEach-Object { ($_ -split ' ')[0] }) -join ', ')" }
elseif (-not $Unattended) {
    Write-Host ""
    $name = Read-Host "      Pick a sign-in name for the website (for example: alex)"
    if ($name) {
        Write-Host "      Now pick a password (6 or more characters). Nothing shows while you type: that's normal."
        & $venvPy -W ignore manage_users.py add $name --admin
    }
} else { Note "no website account made (unattended run). Make one with: venv\Scripts\python manage_users.py add NAME --admin" }

# ---------- 8. Check ----------
Step 8 "Checking everything"
& $venvPy -W ignore check_setup.py
$bad = $LASTEXITCODE

Write-Host ""
if ($bad -eq 0 -and $failed.Count -eq 0) {
    Write-Host "All set." -ForegroundColor Green
} else {
    Write-Host "Set up, with things to look at above$(if ($failed.Count) { ': ' + ($failed -join ', ') })." -ForegroundColor Yellow
}
Write-Host "  On this computer only:        double-click  Start Video Maker.bat"
Write-Host "  From a phone or anywhere:     double-click  Start Website.bat  and use the link it shows"
Write-Host "  Something not working later:  venv\Scripts\python check_setup.py"
exit $(if ($bad -eq 0) { 0 } else { 1 })
