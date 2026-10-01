@echo off
REM Abre la interfaz de envolventes de carga viva DESDE EL CODIGO FUENTE.
REM Para el uso normal, prefiera "Envolventes de Carga Viva.exe": no necesita
REM Python instalado. Este .bat es el respaldo para cuando se edita el codigo
REM (motor_puente.py, gui_puente.py, etc.) y se quiere probar sin recompilar.
REM Se situa en la carpeta de este archivo, aunque la ruta tenga espacios o tildes.
cd /d "%~dp0"

py gui_puente.py
if errorlevel 1 (
    echo.
    echo ---------------------------------------------------------------
    echo La interfaz termino con un error. Revise el mensaje de arriba.
    echo Si dice "No module named ...", instale lo que falte con:
    echo     py -m pip install numpy pandas matplotlib openpyxl pillow
    echo ---------------------------------------------------------------
    pause
)
