@echo off
setlocal enabledelayedexpansion
title NotionSearch

rem Starts NotionSearch and opens it in the browser.
rem This is what the Start Menu shortcut runs.

set "PORT=8080"
set "REPO=%~dp0.."
set "COMPOSE=%REPO%\docker"

echo.
echo   NotionSearch
echo   ------------
echo.

rem --- is Docker installed? -------------------------------------------------
where docker >nul 2>&1
if errorlevel 1 (
    echo   Docker Desktop does not appear to be installed.
    echo.
    echo   NotionSearch needs it to run. Easiest fix: re-run the NotionSearch
    echo   installer and leave "Install Docker Desktop" ticked - it downloads
    echo   and installs it for you.
    echo.
    echo   To install it from here instead:
    echo       powershell -ExecutionPolicy Bypass -File "%~dp0install-windows.ps1"
    echo.
    echo   Or download it yourself from
    echo       https://www.docker.com/products/docker-desktop/
    echo.
    pause
    exit /b 1
)

rem --- is the Docker engine running? ---------------------------------------
docker info >nul 2>&1
if errorlevel 1 (
    echo   Starting Docker Desktop...
    if exist "%ProgramFiles%\Docker\Docker\Docker Desktop.exe" (
        start "" "%ProgramFiles%\Docker\Docker\Docker Desktop.exe"
    ) else (
        echo   Could not find Docker Desktop. Please start it yourself.
    )

    echo   Waiting for Docker to be ready. This can take a minute...
    set /a waited=0
    :waitdocker
    timeout /t 3 /nobreak >nul
    set /a waited+=3
    docker info >nul 2>&1
    if not errorlevel 1 goto dockerready
    if !waited! geq 180 (
        echo.
        echo   Docker did not start within 3 minutes.
        echo   Open Docker Desktop, wait for "Engine running", then try again.
        echo.
        pause
        exit /b 1
    )
    goto waitdocker
)
:dockerready
echo   Docker is running.

rem --- read the port from docker\.env if it is set there --------------------
if exist "%COMPOSE%\.env" (
    for /f "usebackq tokens=1,2 delims==" %%A in ("%COMPOSE%\.env") do (
        if /i "%%A"=="PORT" set "PORT=%%B"
    )
)

rem --- start ----------------------------------------------------------------
echo   Starting NotionSearch...
echo   ^(the first run builds the image and takes a few minutes^)
echo.

pushd "%COMPOSE%"
docker compose up -d
set "RC=%errorlevel%"
popd

if not "%RC%"=="0" (
    echo.
    echo   Failed to start. To see why, run:
    echo       cd "%COMPOSE%" ^&^& docker compose logs
    echo.
    pause
    exit /b 1
)

rem --- wait for it to answer, then open the browser -------------------------
echo   Waiting for it to come up...
set /a tries=0
:waitapp
timeout /t 2 /nobreak >nul
set /a tries+=1
curl -s -o nul -f "http://localhost:%PORT%/health" >nul 2>&1
if not errorlevel 1 goto appready
if !tries! geq 60 (
    echo.
    echo   It has not responded yet, but may still be starting.
    echo   Try http://localhost:%PORT% in a minute.
    echo.
    pause
    exit /b 0
)
goto waitapp

:appready
echo.
echo   NotionSearch is running at http://localhost:%PORT%
echo.
start "" "http://localhost:%PORT%"

echo   It keeps running in the background - you can close this window.
echo   To stop it, use the "Stop NotionSearch" shortcut.
echo.
timeout /t 8 /nobreak >nul
exit /b 0
