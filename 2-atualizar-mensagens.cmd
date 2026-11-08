@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "WACLI_STORE_DIR=%USERPROFILE%\.wacli"
echo.
echo  ATUALIZAR AS MENSAGENS
echo  ----------------------
echo  Baixa as mensagens novas para o computador e fecha quando terminar.
echo  Nada e enviado. O celular precisa estar ligado e com internet.
echo.
bin\wacli.exe sync --once
echo.
echo  Pronto. Agora peca ao Claude: /resumo-whatsapp
echo.
pause
