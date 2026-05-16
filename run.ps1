$env:PYTHONIOENCODING = "utf-8"
$root = $PSScriptRoot

# Silent launch via pythonw - no console window, floating-ball only.
Start-Process -FilePath "$root\.venv\Scripts\pythonw.exe" `
    -ArgumentList "$root\voice2text.py" `
    -WindowStyle Hidden

Write-Host "voice2text started (look for the floating ball on your desktop)."
Write-Host "To stop: right-click the ball -> exit menu"
