"""Package the working controller for this Mac, without private runtime data."""
from pathlib import Path
import shutil,sys,plistlib,subprocess,os
BASE=Path(__file__).resolve().parents[1]
TARGET=Path.home()/'Applications/武陵居中控.app'
if TARGET.exists():raise SystemExit('App already exists; preserve it before rebuilding')
resources=TARGET/'Contents/Resources';core=resources/'controller';core.mkdir(parents=True)
for name in ['backend','web','assets']:
 shutil.copytree(BASE/name,core/name,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
for name in ['README.md','THIRD_PARTY.md','requirements.txt']:shutil.copy2(BASE/name,core/name)
shutil.copytree(BASE/'docs',core/'docs')
# Vendored site-packages keep the installed app independent of the source checkout.
# The Python 3.12 interpreter remains the one already installed on this Mac.
lib=Path(sys.prefix)/'lib/python3.12/site-packages'
shutil.copytree(lib,resources/'lib',ignore=shutil.ignore_patterns('__pycache__','*.pyc','pymupdf','pymupdf-*','pytest','_pytest','pytest-*','pytest_asyncio','pytest_asyncio-*','pip','pip-*','iniconfig','iniconfig-*','pluggy','pluggy-*','pygments','pygments-*','_distutils_hack','setuptools','setuptools-*','pkg_resources'))
macos=TARGET/'Contents/MacOS';macos.mkdir()
launcher=macos/'WulingHome'
subprocess.run(['/usr/bin/xcrun','swiftc',str(BASE/'macos/main.swift'),'-o',str(launcher)],check=True)
info={'CFBundleName':'武陵居中控','CFBundleDisplayName':'武陵居中控','CFBundleIdentifier':'tw.wuling.homecontrol','CFBundleVersion':'1','CFBundleShortVersionString':'1.0.0','CFBundleExecutable':'WulingHome','CFBundlePackageType':'APPL','LSUIElement':False,'NSLocalNetworkUsageDescription':'連接武陵居家中的智慧開關、燈具與音箱，讀取狀態並執行您操作的控制。','NSBonjourServices':['_googlecast._tcp'],'NSHighResolutionCapable':True,'CFBundleIconFile':'HomeIcon','LSMinimumSystemVersion':'13.0'}
(TARGET/'Contents/Info.plist').write_bytes(plistlib.dumps(info))
# Draw the code-native house mark from the same SVG as the web app.
import pymupdf
iconset=BASE/'work/HomeIcon.iconset';iconset.mkdir(parents=True,exist_ok=True)
svg=(BASE/'frontend/public/app-icon.svg').read_bytes();doc=pymupdf.open(stream=svg,filetype='svg')
for points in [16,32,128,256,512]:
 for scale in [1,2]:
  pixels=points*scale;pix=doc[0].get_pixmap(matrix=pymupdf.Matrix(pixels/512,pixels/512),alpha=True)
  pix.save(iconset/f'icon_{points}x{points}{"@2x" if scale==2 else ""}.png')
subprocess.run(['/usr/bin/iconutil','-c','icns',str(iconset),'-o',str(resources/'HomeIcon.icns')],check=True)
subprocess.run(['/usr/bin/codesign','--force','--sign','-',str(TARGET)],check=True)
print(TARGET)
