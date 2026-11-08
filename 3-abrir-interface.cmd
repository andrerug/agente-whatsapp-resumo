@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo.
echo  INTERFACE DO RESUMO DE MENSAGENS (somente leitura)
echo  --------------------------------------------------
echo  O navegador vai abrir em http://127.0.0.1:8765
echo  Para encerrar, feche esta janela.
echo.
python -X utf8 interface\app.py
pause
