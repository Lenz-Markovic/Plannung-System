@echo off
rem ======================================================================
rem  Vorfuehrung vorbereiten (Windows) - einfach doppelklicken.
rem  ACHTUNG: loescht alle Demodaten und legt sie frisch an
rem  (Liegenschaften, Montage, Fahrplaene, Rueckmeldungen, Aushaenge, Notizen).
rem  Vorher einmal start_windows.bat ausfuehren (richtet alles ein).
rem ======================================================================
cd /d "%~dp0"
set PY=.venv\Scripts\python.exe
if not exist "%PY%" (
    echo Bitte zuerst start_windows.bat doppelklicken - dort wird alles eingerichtet.
    pause
    exit /b 1
)
echo Die Demodaten werden geloescht und frisch angelegt. Weiter mit einer Taste, abbrechen mit Strg+C.
pause >nul
"%PY%" manage.py demo_vorbereiten --password Test-Passwort-2026 || goto fehler
echo.
echo Fertig. Jetzt start_windows.bat doppelklicken und anmelden, z. B. dispo_demo / Test-Passwort-2026
pause
exit /b 0

:fehler
echo.
echo FEHLER. Bitte den roten Text oben kopieren und an Claude schicken.
pause
exit /b 1
