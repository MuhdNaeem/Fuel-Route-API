@echo off
echo Running Fuel Route API tests...
call "%~dp0venv\Scripts\activate.bat"
python "%~dp0manage.py" test routing
pause
