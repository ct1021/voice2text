$env:PYTHONIOENCODING = "utf-8"
chcp 65001 | Out-Null
$root = $PSScriptRoot

# Debug mode: console attached, logs visible live.
& "$root\.venv\Scripts\python.exe" "$root\voice2text.py"
