@echo off
rem ONE FILE TO SET UP EVERYTHING. Save it anywhere (the Desktop is fine) and double-click it.
rem It downloads the Video Maker from GitHub and installs it. Safe to run again: it then updates instead.
rem Needs: Windows 10 or 11, internet, and a GitHub account that was invited to the project.
setlocal
set "VM_SELF=%~f0"
powershell -NoProfile -ExecutionPolicy Bypass -Command "iex ((Get-Content -Raw -LiteralPath $env:VM_SELF) -split '(?m)^#POWERSHELL#\s*$', 2)[1]"
echo.
pause
exit /b

#POWERSHELL#
# Everything below runs in PowerShell. Settings can be overridden for testing with environment variables.
$ErrorActionPreference = "Stop"
$repo   = if ($env:VM_REPO)   { $env:VM_REPO }   else { "YOUR-NAME/video-maker" }
$target = if ($env:VM_TARGET) { $env:VM_TARGET } else { Join-Path $env:USERPROFILE "VideoMaker" }
$unattended = [bool]$env:VM_UNATTENDED

function Say($t)  { Write-Host ""; Write-Host $t -ForegroundColor Cyan }
function Ok($t)   { Write-Host "   OK: $t" -ForegroundColor Green }
function Stop-With($t) { Write-Host ""; Write-Host $t -ForegroundColor Red; exit 1 }

Write-Host "Video Maker setup" -ForegroundColor White
Write-Host "It will be installed in: $target"

if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
    Stop-With "This computer doesn't have 'winget'. Open the Microsoft Store, install 'App Installer', then double-click this file again."
}

# ---- Git and the GitHub tool ----
Say "Step 1 of 4: the download tools (Git and GitHub)"
$gitDir = "C:\Program Files\Git\cmd"; $ghDir = "C:\Program Files\GitHub CLI"
if (-not (Test-Path "$gitDir\git.exe") -and -not (Get-Command git -ErrorAction SilentlyContinue)) {
    Write-Host "   installing Git..."; & winget install -e --id Git.Git --accept-source-agreements --accept-package-agreements --silent | Out-Null
}
if (-not (Test-Path "$ghDir\gh.exe") -and -not (Get-Command gh -ErrorAction SilentlyContinue)) {
    Write-Host "   installing the GitHub tool..."; & winget install -e --id GitHub.cli --accept-source-agreements --accept-package-agreements --silent | Out-Null
}
$env:Path = "$gitDir;$ghDir;$env:Path"   # a fresh install isn't on the path of this window yet
if (-not (Get-Command git -ErrorAction SilentlyContinue)) { Stop-With "Git didn't install. Close this window and double-click the file again. If it still fails, restart the computer first." }
if (-not (Get-Command gh -ErrorAction SilentlyContinue))  { Stop-With "The GitHub tool didn't install. Close this window and double-click the file again." }
Ok "Git and the GitHub tool are ready"

# ---- Sign in to GitHub (her own account, in her own browser) ----
Say "Step 2 of 4: sign in to GitHub"
$ErrorActionPreference = "Continue"
& gh auth status 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    if ($unattended) { Stop-With "Not signed in to GitHub (unattended run can't sign in)." }
    Write-Host "   The project is private, so GitHub needs to know it's you."
    Write-Host "   A code will appear below. Press Enter, your browser opens: paste the code, sign in, click Authorize." -ForegroundColor White
    & gh auth login --hostname github.com --git-protocol https --web
    if ($LASTEXITCODE -ne 0) { Stop-With "The GitHub sign-in didn't finish. Double-click this file to try again." }
}
& gh auth setup-git 2>$null | Out-Null
Ok "signed in to GitHub"

# ---- Download, or update ----
Say "Step 3 of 4: get the Video Maker"
if (Test-Path (Join-Path $target ".git")) {
    Write-Host "   already here: getting the latest version..."
    & git -C $target pull --ff-only 2>&1 | Select-Object -Last 1 | ForEach-Object { Write-Host "   $_" }
    if ($LASTEXITCODE -ne 0) { Write-Host "   Couldn't update (keeping the version that's here)." -ForegroundColor Yellow }
} elseif (Test-Path $target) {
    Stop-With "A folder named $target already exists but isn't the Video Maker. Rename or remove it, then try again."
} else {
    & gh repo clone $repo $target -- --quiet
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path (Join-Path $target "install.ps1"))) {
        Stop-With "Couldn't download the project. The GitHub account you signed in with needs an invitation to '$repo' - ask the maintainer to invite it, accept the invitation email, then double-click this file again."
    }
}
Ok "the project is in $target"

# ---- Install ----
Say "Step 4 of 4: install (the long part: about 1.5 GB of downloads)"
$ErrorActionPreference = "Stop"
$installArgs = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $target "install.ps1"))
if ($unattended) { $installArgs += "-Unattended" }
& powershell @installArgs
$code = $LASTEXITCODE

# ---- Desktop shortcuts, so she never has to find the folder again ----
try {
    $desk = [Environment]::GetFolderPath("Desktop"); $sh = New-Object -ComObject WScript.Shell
    foreach ($s in @(@("Video Maker", "Start Video Maker.bat"), @("Video Maker (phone link)", "Start Website.bat"))) {
        $l = $sh.CreateShortcut((Join-Path $desk ($s[0] + ".lnk"))); $l.TargetPath = (Join-Path $target $s[1]); $l.WorkingDirectory = $target; $l.Save()
    }
    Ok "added 'Video Maker' and 'Video Maker (phone link)' to the Desktop"
} catch { Write-Host "   (couldn't add Desktop shortcuts; use the files in $target instead)" -ForegroundColor Yellow }

Write-Host ""
if ($code -eq 0) { Write-Host "Done. Double-click 'Video Maker' on the Desktop to start." -ForegroundColor Green }
else { Write-Host "Installed, with something to look at above. To re-check any time: double-click this file again." -ForegroundColor Yellow }
exit $code
