@echo off
rem memory-recall hook launcher (Windows)
rem Resolves python without relying on a specific install path, then runs recall.py hook.
setlocal
set "HERE=%~dp0"
set "TOOL=%HERE%..\tools\recall.py"
set "PYEXE="

where python >nul 2>nul && set "PYEXE=python"
if not defined PYEXE (
  where py >nul 2>nul && set "PYEXE=py -3"
)
if not defined PYEXE (
  if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PYEXE=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
)
if not defined PYEXE (
  if exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" set "PYEXE=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
)
if not defined PYEXE exit /b 0

set PYTHONIOENCODING=utf-8
%PYEXE% "%TOOL%" hook --host codex %*
exit /b 0
