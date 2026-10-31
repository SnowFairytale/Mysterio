@echo off
rem Build a portable folder at dist\MysterioPet\ (no Python needed on the target PC).
rem Requires: pip install -r requirements-dev.txt
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo Python was not found. Install Python 3.10+ or activate your virtualenv first.
  pause
  exit /b 1
)

rem conda puts its DLLs in Library\bin; without it the frozen app misses pyexpat / libssl.
if defined CONDA_PREFIX (
  set "PATH=%CONDA_PREFIX%;%CONDA_PREFIX%\Library\bin;%CONDA_PREFIX%\Library\mingw-w64\bin;%CONDA_PREFIX%\Scripts;%CONDA_PREFIX%\DLLs;%PATH%"
)

python -c "import PyInstaller" >nul 2>nul
if errorlevel 1 (
  echo Installing PyInstaller...
  python -m pip install pyinstaller
)

python -c "import PIL" >nul 2>nul
if errorlevel 1 (
  echo Installing Pillow for icon generation...
  python -m pip install pillow
)

echo [1/3] Generating icon...
python -c "from pathlib import Path; from PIL import Image; src=Path('miastria_chibi/poses/idle.png'); out=Path('build/pet.ico'); out.parent.mkdir(parents=True, exist_ok=True); im=Image.open(src).convert('RGBA'); box=im.getchannel('A').point(lambda p: 255 if p>20 else 0).getbbox(); im=im.crop(box); side=max(im.size); canvas=Image.new('RGBA',(side,side),(0,0,0,0)); canvas.alpha_composite(im,((side-im.width)//2,(side-im.height)//2)); canvas.save(out, sizes=[(256,256),(128,128),(64,64),(48,48),(32,32),(16,16)]); print('icon ->', out)"

echo [2/3] Building (first run takes a few minutes)...
python -m PyInstaller --noconfirm --clean --onedir --windowed --name MysterioPet ^
  --exclude-module numpy --exclude-module matplotlib --exclude-module pandas ^
  --exclude-module PIL --exclude-module jedi --exclude-module zmq ^
  --exclude-module tkinter --exclude-module IPython --exclude-module scipy ^
  --icon "%~dp0build\pet.ico" ^
  --add-data "%~dp0miastria_chibi\poses;miastria_chibi\poses" ^
  --distpath "%~dp0dist" --workpath "%~dp0build\pyinstaller" --specpath "%~dp0build" ^
  "%~dp0main.py"
if errorlevel 1 goto :fail

echo [3/3] Done: dist\MysterioPet\
echo Zip that folder to share it. config.json is created next to the exe on first run.
exit /b 0

:fail
echo Build failed. Check the messages above.
pause
exit /b 1
