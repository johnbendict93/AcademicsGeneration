@echo off
setlocal
cd /d "%~dp0"
set "ACT="
for %%P in ("%USERPROFILE%\anaconda3" "%LOCALAPPDATA%\anaconda3" "%ProgramData%\anaconda3" "%ProgramData%\Anaconda3" "%USERPROFILE%\miniconda3" "%LOCALAPPDATA%\miniconda3" "C:\anaconda3") do (
  if not defined ACT if exist "%%~P\Scripts\activate.bat" set "ACT=%%~P\Scripts\activate.bat"
)
if not defined ACT (
  echo Anaconda not found. & pause & exit /b 1
)
call "%ACT%"
set PYTHONIOENCODING=utf-8
echo Starting AcademicsGen... your browser will open at http://localhost:8501
echo Keep this window open while you use the app. Close it to stop the app.
python -m streamlit run academicsgen_app.py --server.port 8501
pause
