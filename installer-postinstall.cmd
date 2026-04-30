@echo off
setlocal EnableExtensions
cd /d "%~dp0"
REM נקרא בסוף אשף Inno — מתקין חבילות מ-requirements.txt. אינטרנט נדרש פעם אחת.
python -m pip install -r requirements.txt
if errorlevel 1 (
  py -3 -m pip install -r requirements.txt
  if errorlevel 1 exit /b 1
)
exit /b 0
