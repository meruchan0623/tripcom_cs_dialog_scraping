@echo off
setlocal
set "ROOT_DIR=%~dp0"
set "PYTHONPATH=%ROOT_DIR%;%PYTHONPATH%"
python -m im_archive_cli.imx_cli %*
endlocal
