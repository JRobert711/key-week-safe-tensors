@echo off
setlocal
title ToolLighthouse - Backend

rem Colocar este archivo en la carpeta ToolLighthouse (junto a backend\ y frontend\).
cd /d "%~dp0backend" || (
  echo No se encontro la carpeta backend junto a este archivo.
  pause
  exit /b 1
)

if not exist "src\server.py" (
  echo No se encontro backend\src\server.py
  pause
  exit /b 1
)

rem Activa un entorno virtual si existe (el primero que se encuentre).
set "VENV_FOUND="
for %%V in (".venv" "venv" "..\.venv" "..\venv" ".env") do (
  if exist "%%~V\Scripts\activate.bat" if not defined VENV_FOUND (
    call "%%~V\Scripts\activate.bat"
    set "VENV_FOUND=%%~V"
  )
)
if defined VENV_FOUND (echo Entorno virtual: %VENV_FOUND%) else (echo Sin entorno virtual: se usa el Python del sistema.)

set "PY=python"
where python >nul 2>&1 || set "PY=py -3"
%PY% --version >nul 2>&1 || (
  echo No se encontro Python. Instalalo o activa tu entorno virtual.
  pause
  exit /b 1
)

echo.
echo Iniciando backend en http://127.0.0.1:8000 (Ctrl+C para detener)
echo.
%PY% src\server.py

echo.
echo El backend se detuvo.
pause
