@echo off
rem ======================================================================
rem  Planungssystem starten (Windows) - einfach doppelklicken.
rem  Beim ersten Start wird alles eingerichtet (dauert ein paar Minuten):
rem  Python-Umgebung, Pakete, Datenbank, Rollen, Test-Benutzer, Demodaten.
rem  Danach startet nur noch der Server und der Browser oeffnet sich.
rem  Beenden: dieses Fenster schliessen (oder Strg+C).
rem ======================================================================
cd /d "%~dp0"
set PY=.venv\Scripts\python.exe

if not exist "%PY%" (
    echo Erste Einrichtung: Python-Umgebung wird angelegt ...
    py -3.12 -m venv .venv 2>nul
    if not exist "%PY%" python -m venv .venv
)
if not exist "%PY%" (
    echo.
    echo FEHLER: Python wurde nicht gefunden.
    echo Bitte Python 3.12 installieren, z. B. aus dem Microsoft Store: "Python 3.12".
    pause
    exit /b 1
)

if not exist ".env" copy ".env.example" ".env" >nul

if not exist ".setup_done" (
    echo Pakete werden installiert ...
    "%PY%" -m pip install -r requirements.txt || goto fehler
    "%PY%" manage.py migrate || goto fehler
    "%PY%" manage.py setup_roles || goto fehler
    "%PY%" manage.py create_demo_users --password Test-Passwort-2026 || goto fehler
    "%PY%" manage.py import_prototype || goto fehler
    echo fertig > ".setup_done"
) else (
    rem Bei spaeteren Starts: neue Pakete und Datenbank-Aenderungen uebernehmen
    "%PY%" -m pip install -q -r requirements.txt
    "%PY%" manage.py migrate
)

echo.
echo ================================================================
echo  Das Planungssystem laeuft:  http://127.0.0.1:8000
echo  Anmelden z. B. mit  admin_demo  /  Test-Passwort-2026
echo  Zum Beenden dieses Fenster schliessen.
echo ================================================================
start "" cmd /c "timeout /t 4 >nul & start http://127.0.0.1:8000"
"%PY%" manage.py runserver
pause
exit /b 0

:fehler
echo.
echo FEHLER bei der Einrichtung. Bitte den roten Text oben kopieren und an Claude schicken.
echo Danach diese Datei einfach noch einmal doppelklicken.
pause
exit /b 1
