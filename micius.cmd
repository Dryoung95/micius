@echo off
setlocal
cd /d "%~dp0"
python -m local_agent.cli %*
