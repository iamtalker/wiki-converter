@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
where python >nul 2>nul && (python app.py & goto :eof)
where py >nul 2>nul && (py -3 app.py & goto :eof)
echo 파이썬(Python 3.9 이상)이 필요합니다. https://www.python.org/downloads/ 에서 설치한 뒤 다시 눌러 주세요.
pause
