"""위키 편집기를 파이썬 없이 도는 단독 exe(위키편집기.exe)로 묶는다 — PyInstaller 가 설치돼 있어야 한다.

    python src/tools/make_icon.py     # 아이콘이 없으면 먼저
    python src/tools/build_exe.py     # → 프로젝트 맨 위 폴더에 위키편집기.exe
"""
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # src 폴더
ROOT = os.path.dirname(HERE)                                         # 프로젝트 맨 위 폴더(exe 가 놓이는 곳)


def main():
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("PyInstaller 가 필요합니다:  pip install pyinstaller")
        return 1
    icon = os.path.join(HERE, "assets", "icon.ico")
    if not os.path.exists(icon):
        subprocess.check_call([sys.executable, os.path.join(HERE, "tools", "make_icon.py")])
    work = tempfile.mkdtemp(prefix="wikiedit-build-")
    try:
        subprocess.check_call([
            sys.executable, "-m", "PyInstaller", "--noconfirm", "--onefile", "--windowed", "--name", "WikiEditor",
            "--icon", icon, "--add-data", os.path.join(HERE, "assets") + os.pathsep + "assets",
            "--workpath", os.path.join(work, "build"), "--specpath", work, "--distpath", os.path.join(work, "dist"),
            os.path.join(HERE, "위키편집기.pyw")])
        out = os.path.join(ROOT, "위키편집기.exe")
        shutil.copyfile(os.path.join(work, "dist", "WikiEditor.exe"), out)
        print("만들었습니다:", out)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
