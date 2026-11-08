@echo off
chcp 65001 >nul
echo.
echo  LOGIN DO CLAUDE PARA O RESUMO AUTOMATICO (feito uma vez so)
echo  -----------------------------------------------------------
echo  O navegador vai abrir na pagina da Anthropic. Entre com a sua conta do Claude
echo  e autorize. Depois volte para a interface e clique em Resumir.
echo.
powershell -NoProfile -Command "$c = Get-Command claude -ErrorAction SilentlyContinue; if ($c) { $exe = $c.Source } else { $exe = Get-ChildItem \"$env:APPDATA\Claude\claude-code\" -Recurse -Filter claude.exe | Sort-Object LastWriteTime -Descending | Select-Object -First 1 -ExpandProperty FullName }; if (-not $exe) { Write-Host 'Claude Code nao encontrado.'; exit 1 }; & $exe auth login; & $exe auth status"
echo.
pause
