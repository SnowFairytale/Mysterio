@echo off
rem 打包成免安装的文件夹版（输出到 dist\MysterioPet\）
rem 依赖：pip install pyinstaller pillow
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo 没找到 python。请先安装 Python 3.10+，或先激活虚拟环境。
  pause
  exit /b 1
)

rem conda 环境下 DLL 在 Library\bin 里，不加入 PATH 会打包出缺 pyexpat/libssl 的程序
if defined CONDA_PREFIX (
  set "PATH=%CONDA_PREFIX%;%CONDA_PREFIX%\Library\bin;%CONDA_PREFIX%\Library\mingw-w64\bin;%CONDA_PREFIX%\Scripts;%CONDA_PREFIX%\DLLs;%PATH%"
)

python -c "import PyInstaller" >nul 2>nul
if errorlevel 1 (
  echo 安装 PyInstaller…
  python -m pip install pyinstaller
)

python -c "import PIL" >nul 2>nul
if errorlevel 1 (
  echo 安装 Pillow（生成图标用）…
  python -m pip install pillow
)

echo [1/3] 生成图标…
python -c "from pathlib import Path; from PIL import Image; src=Path('miastria_chibi/poses/idle.png'); out=Path('build/pet.ico'); out.parent.mkdir(parents=True, exist_ok=True); im=Image.open(src).convert('RGBA'); box=im.getchannel('A').point(lambda p: 255 if p>20 else 0).getbbox(); im=im.crop(box); side=max(im.size); canvas=Image.new('RGBA',(side,side),(0,0,0,0)); canvas.alpha_composite(im,((side-im.width)//2,(side-im.height)//2)); canvas.save(out, sizes=[(256,256),(128,128),(64,64),(48,48),(32,32),(16,16)]); print('icon ->', out)"

echo [2/3] 打包中（首次约 1~3 分钟）…
python -m PyInstaller --noconfirm --clean --onedir --windowed --name MysterioPet ^
  --exclude-module numpy --exclude-module matplotlib --exclude-module pandas ^
  --exclude-module PIL --exclude-module jedi --exclude-module zmq ^
  --exclude-module tkinter --exclude-module IPython --exclude-module scipy ^
  --icon "%~dp0build\pet.ico" ^
  --add-data "%~dp0miastria_chibi\poses;miastria_chibi\poses" ^
  --distpath "%~dp0dist" --workpath "%~dp0build\pyinstaller" --specpath "%~dp0build" ^
  "%~dp0main.py"
if errorlevel 1 goto :fail

echo [3/3] 完成：dist\MysterioPet\
echo 整个文件夹一起压缩发给别人即可，对方不需要装 Python。
echo 首次运行会在 exe 旁边生成 config.json。
exit /b 0

:fail
echo 打包失败，请检查上面的报错。
exit /b 1
