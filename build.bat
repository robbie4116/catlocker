@echo off

py -m PyInstaller ^
    --name=CatLocker ^
    --onefile ^
    --noconsole ^
    --icon=assets/icon.ico ^
    --add-data="assets/icon.ico;assets" ^
    main.py
if errorlevel 1 exit /b %errorlevel%
