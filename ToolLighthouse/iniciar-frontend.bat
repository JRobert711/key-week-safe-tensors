@echo off
setlocal
title ToolLighthouse - Frontend

rem Colocar este archivo en la carpeta ToolLighthouse (junto a backend\ y frontend\).
cd /d "%~dp0frontend" || (
  echo No se encontro la carpeta frontend junto a este archivo.
  pause
  exit /b 1
)

if not exist "package.json" (
  echo No se encontro frontend\package.json
  pause
  exit /b 1
)

where npm >nul 2>&1 || (
  echo No se encontro npm. Instala Node.js desde https://nodejs.org
  pause
  exit /b 1
)

if not exist "node_modules" (
  echo Instalando dependencias, esto solo ocurre la primera vez...
  call npm install || (
    echo Fallo npm install.
    pause
    exit /b 1
  )
)

rem El proxy necesita BACKEND_URL. Solo se define si no hay .env.local ni variable de entorno.
if not exist ".env" if not defined BACKEND_URL (
  set "BACKEND_URL=http://127.0.0.1:8000"
  echo Sin .env.local: se usa BACKEND_URL=%BACKEND_URL%
)

echo.
echo Iniciando frontend en http://localhost:3000 (Ctrl+C para detener)
echo.
call npm run dev

echo.
echo El frontend se detuvo.
pause
