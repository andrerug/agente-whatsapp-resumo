@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "WACLI_STORE_DIR=%USERPROFILE%\.wacli"
echo.
echo  CONECTAR O WHATSAPP (feito uma vez so)
echo  -------------------------------------
echo  1. No celular: WhatsApp ^> Configuracoes ^> Dispositivos conectados ^> Conectar dispositivo.
echo  2. Aponte a camera para o QR code que vai aparecer abaixo.
echo  3. Espere a primeira sincronizacao terminar (a janela fecha sozinha quando acabar).
echo.
bin\wacli.exe auth --history-days 30
echo.
bin\wacli.exe auth status
echo.
pause
