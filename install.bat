@echo off
chcp 65001 >nul
echo.
echo  =====================================================
echo   Football Predictor - התקנה אוטומטית מלאה
echo  =====================================================
echo.

:: בדיקה שפייתון מותקן
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo  שגיאה: פייתון לא מותקן!
    echo  הורד מ: https://www.python.org/downloads/
    echo  חשוב: בסוף ההתקנה סמן "Add Python to PATH"
    pause
    exit /b 1
)

:: בדיקה שגיט מותקן
git --version >nul 2>&1
if %errorlevel% neq 0 (
    echo  שגיאה: Git לא מותקן!
    echo  הורד מ: https://git-scm.com/download/win
    pause
    exit /b 1
)

echo  [1/7] מוריד את הקוד מ-GitHub...
git clone https://github.com/liorlior1223/football-predictor.git
if %errorlevel% neq 0 (
    echo  שגיאה בהורדת הקוד. בדוק חיבור אינטרנט.
    pause
    exit /b 1
)
cd football-predictor

echo.
echo  [2/7] יוצר סביבת פייתון...
python -m venv venv
call venv\Scripts\activate.bat

echo.
echo  [3/7] מתקין חבילות (כ-2 דקות)...
pip install --upgrade pip -q
pip install -r requirements.txt -q

echo.
echo  [4/7] מאתחל מסד נתונים...
python scripts/init_db.py

echo.
echo  [5/7] שולף נתונים היסטוריים (10-20 דקות)...
python scripts/fetch_data.py --historical

echo.
echo  [6/7] מחשב פיצרים...
python scripts/compute_features.py

echo.
echo  [7/7] מאמן מודלים...
python scripts/train_model.py

echo.
echo  =====================================================
echo   הכל מוכן!
echo   מפעיל שרת...
echo   פתח בדפדפן: http://localhost:8000
echo  =====================================================
echo.
python scripts/run_server.py

pause
