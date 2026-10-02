@echo off
chcp 65001 >nul
title LDMV
cd /d "%~dp0"

set PY=python
where py >nul 2>nul && set PY=py

if exist ".venv\installe.ok" goto lancer
echo.
echo  Premiere utilisation : installation de LDMV, cela prend quelques minutes...
echo.
%PY% -m venv .venv || goto erreur
".venv\Scripts\python.exe" -m pip install --upgrade pip || goto erreur
".venv\Scripts\python.exe" -m pip install -e ".[ui]" || goto erreur
echo ok> ".venv\installe.ok"

:lancer
echo  Lancement de LDMV...
".venv\Scripts\python.exe" -m ldmv %*
if errorlevel 1 pause
goto :eof

:erreur
echo.
echo  ERREUR pendant l installation.
echo  Verifiez que Python est installe depuis python.org
echo  en cochant la case "Add Python to PATH", puis relancez ce fichier.
pause
