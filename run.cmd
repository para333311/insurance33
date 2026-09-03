@echo off
chcp 65001 >nul
cd /d D:\insurance33
if not exist logs mkdir logs
set PATH=C:\Program Files\Git\mingw64\bin;C:\Program Files\Git\cmd;%PATH%
set PYTHONIOENCODING=utf-8
echo ===== %date% %time% >> logs\run.log
git pull -q >> logs\run.log 2>&1
python scan.py >> logs\run.log 2>&1
python report.py >> logs\run.log 2>&1
git add data >> logs\run.log 2>&1
git commit -q -m "scan %date%" >> logs\run.log 2>&1
git push -q >> logs\run.log 2>&1
