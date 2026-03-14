@echo off
chcp 437 >nul
echo.
echo  =====================================================
echo   Football Predictor - Automatic Setup
echo  =====================================================
echo.

python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo  ERROR: Python not found.
    echo  Download from: https://www.python.org/downloads/
    pause
    exit /b 1
)

git --version >nul 2>&1
if %errorlevel% neq 0 (
    echo  ERROR: Git not found.
    echo  Download from: https://git-scm.com/download/win
    pause
    exit /b 1
)

echo  [1/7] Downloading code from GitHub...
if exist football-predictor (
    echo  Folder already exists, updating...
    cd football-predictor
    git pull origin claude/football-prediction-system-gzO43
) else (
    git clone -b claude/football-prediction-system-gzO43 https://github.com/liorlior1223/football-predictor.git
    if %errorlevel% neq 0 (
        echo  ERROR: Could not download code. Check internet connection.
        pause
        exit /b 1
    )
    cd football-predictor
)

echo.
echo  [2/7] Creating Python environment...
python -m venv venv
call venv\Scripts\activate.bat

echo.
echo  [3/7] Installing packages (2-3 minutes)...
pip install --upgrade pip -q
pip install -r requirements.txt -q

echo.
echo  [4/7] Setting up database...
python scripts\init_db.py

echo.
echo  [5/7] Downloading match data (10-20 minutes)...
python scripts\fetch_data.py --historical

echo.
echo  [6/7] Computing features...
python scripts\compute_features.py

echo.
echo  [7/7] Training prediction models...
python scripts\train_model.py

echo.
echo  =====================================================
echo   DONE! Starting server...
echo   Open in browser: http://localhost:8000
echo  =====================================================
echo.
python scripts\run_server.py

pause
