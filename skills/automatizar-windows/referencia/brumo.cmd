@echo off
rem Mantiene a Brumo corriendo: si se cae (sin internet, Ollama apagado...), lo vuelve a arrancar a los 30 segundos.
rem Ollama (la IA) lo enciende brumo-oculto.vbs; aquí solo se espera a que esté listo. Registro: data\brumo.log.
cd /d "%~dp0..\.."
set PYTHONIOENCODING=utf-8
if not exist data mkdir data
:inicio
for /l %%i in (1,1,30) do curl -s -m 2 http://127.0.0.1:11434/api/version >nul 2>&1 && goto listo || ping -n 2 127.0.0.1 >nul
:listo
echo ==== %date% %time% arrancando Brumo >> data\brumo.log
".venv\Scripts\python.exe" -m brumo >> data\brumo.log 2>&1
ping -n 31 127.0.0.1 > nul
goto inicio
