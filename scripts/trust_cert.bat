@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0.."

set "CA=%CD%\certs\jiudou-ca.crt"
if not exist "%CA%" (
  echo [!] 未找到根证书：%CA%
  echo     请先运行：python scripts\gen_cert.py
  echo.
  pause
  exit /b 1
)

echo ============================================================
echo  安装本地根证书到「当前用户 \ 受信任的根证书颁发机构」
echo  %CA%
echo.
echo  作用：消除本平台 https://127.0.0.1:8443 的「不安全」告警。
echo  范围：仅当前 Windows 用户，不影响其他账户与机器级证书。
echo  注意：该根证书的私钥保存在本机 certs\jiudou-ca.key，
echo        仅用于本地开发调试，请勿分发该私钥。
echo.
echo  如需卸载，执行：
echo    certutil -user -delstore Root "Jiudou Local Dev Root CA"
echo ============================================================
echo.
certutil -user -addstore Root "%CA%"
if errorlevel 1 (
  echo.
  echo [!] 自动安装未成功。可手动操作：双击 certs\jiudou-ca.crt
  echo     ^-^> 安装证书 ^-^> 当前用户 ^-^> 将所有的证书都放入下列存储
  echo     ^-^> 浏览 ^-^> 受信任的根证书颁发机构
) else (
  echo.
  echo [OK] 已安装。请重启浏览器，再访问 https://127.0.0.1:8443
)
echo.
pause
