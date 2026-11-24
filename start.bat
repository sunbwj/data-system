@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

rem 优先使用项目自带的虚拟环境（如存在），否则回落到 PATH 中的 python
set "PY=C:\Users\chx\.workbuddy\binaries\python\envs\menuadmin\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

echo ============================================================
echo  九豆高质量数据资产平台 V1.0.0
echo  首次运行请先执行：%PY% scripts\init_db.py
echo ------------------------------------------------------------
echo  HTTP  : http://127.0.0.1:8765
echo  HTTPS : https://127.0.0.1:8443   （certs\ 下有证书时自动启用）
echo.
echo  证书未生成？运行：%PY% scripts\gen_cert.py
echo  浏览器提示不安全？双击：scripts\trust_cert.bat
echo ============================================================
"%PY%" run.py
pause
