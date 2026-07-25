@echo off
title Hashim Bot Dashboard

echo.
echo  ================================================
echo   HASHIM BOT - Trading Dashboard
echo  ================================================
echo.
echo  Starting API server on http://localhost:8000
echo  Starting React UI  on http://localhost:5173
echo.
echo  Press Ctrl+C in each window to stop.
echo.

:: Kill anything already on port 8000 or 5173 so restart always works
for /f "tokens=5" %%p in ('netstat -ano ^| findstr :8000 2^>nul') do taskkill /PID %%p /F >nul 2>&1
for /f "tokens=5" %%p in ('netstat -ano ^| findstr :5173 2^>nul') do taskkill /PID %%p /F >nul 2>&1

:: Start the API server in a new window
start "Hashim Bot API" cmd /k "cd /d %~dp0 && python start_api.py"

:: Give the API a moment to boot
timeout /t 3 /nobreak >nul

:: Start the React dev server in a new window
start "Hashim Bot UI" cmd /k "cd /d %~dp0\frontend && npm run dev"

:: Open browser after a short delay
timeout /t 4 /nobreak >nul
start http://localhost:5173
