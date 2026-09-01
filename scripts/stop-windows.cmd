@echo off
title Stop NotionSearch

rem Stops NotionSearch. Your synced pages and settings are kept.

set "COMPOSE=%~dp0..\docker"

echo.
echo   Stopping NotionSearch...
echo.

where docker >nul 2>&1
if errorlevel 1 (
    echo   Docker is not installed, so nothing is running.
    timeout /t 4 /nobreak >nul
    exit /b 0
)

pushd "%COMPOSE%"
docker compose down
set "RC=%errorlevel%"
popd

echo.
if "%RC%"=="0" (
    echo   Stopped. Your synced pages and settings are kept.
) else (
    echo   Could not stop cleanly. Check Docker Desktop.
)
echo.
timeout /t 5 /nobreak >nul
exit /b 0
