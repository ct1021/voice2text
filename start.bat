@echo off
REM Double-click to launch voice2text silently. Floating ball appears in ~10s.
start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0voice2text.py"
