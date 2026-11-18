@echo off
echo Starting Fuel Route API server on http://127.0.0.1:8000/ ...
call "%~dp0venv\Scripts\activate.bat"
python "%~dp0manage.py" runserver
pause
