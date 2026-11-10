$ErrorActionPreference = "Stop"
if ($env:OS -ne "Windows_NT") { throw "Build this executable on 64-bit Windows." }
Set-Location (Join-Path $PSScriptRoot "..")
$recorderName = "AMSLeagueRecorder-Windows-x64-Intel-AMD"
$executable = Join-Path "dist" "$recorderName.exe"
python -m venv .venv
if ($LASTEXITCODE -ne 0) { throw "Unable to create the build environment." }
& .\.venv\Scripts\python.exe -c "import struct,sysconfig; assert struct.calcsize('P') == 8 and sysconfig.get_platform() == 'win-amd64', 'Use x64 Windows Python'"
if ($LASTEXITCODE -ne 0) { throw "A 64-bit Python installation is required." }
& .\.venv\Scripts\python.exe -m unittest discover -s tests -v
if ($LASTEXITCODE -ne 0) { throw "Recorder tests failed." }
& .\.venv\Scripts\python.exe -m pip install "pyinstaller>=6,<7"
if ($LASTEXITCODE -ne 0) { throw "PyInstaller installation failed." }
& .\.venv\Scripts\python.exe -m PyInstaller --clean --noconfirm --onefile --windowed --noupx --name $recorderName --paths . tools/launcher.py
if ($LASTEXITCODE -ne 0) { throw "The executable build failed." }
& .\.venv\Scripts\python.exe tools/smoke_gui.py $executable
if ($LASTEXITCODE -ne 0) { throw "The packaged recorder window failed its startup check." }
& .\.venv\Scripts\python.exe tools/smoke_update.py $executable
if ($LASTEXITCODE -ne 0) { throw "The packaged recorder failed its self-update check." }
Write-Output "Recorder executable: $executable"
