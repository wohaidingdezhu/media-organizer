# coding: utf-8
"""Build on the target OS; bundle a console backend and a windowed shell."""
from pathlib import Path
import shutil
import importlib.metadata
import re
import subprocess
import sys

BASE = Path(__file__).resolve().parent


def run():
    if sys.platform not in {'darwin', 'win32'}:
        raise ValueError('请在目标 macOS / Windows 上打包')
    desktop = BASE / 'dist' / 'desktop'
    resources = BASE / 'build' / 'desktop-resources'
    resources.mkdir(parents=True, exist_ok=True)
    licenses = resources / 'licenses'
    licenses.mkdir(exist_ok=True)
    for distribution in importlib.metadata.distributions():
        package = re.sub(r'[^A-Za-z0-9_.-]', '_', distribution.metadata['Name'])
        for member in distribution.files or []:
            if member.name.lower().startswith(('license', 'copying', 'notice')):
                source = distribution.locate_file(member)
                if source.is_file():
                    target = licenses / (package + '-' + member.name)
                    target.write_bytes(source.read_bytes())
    import imageio_ffmpeg
    license_output = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-L'], capture_output=True, text=True, encoding='utf-8', check=True)
    (licenses / 'FFmpeg-license.txt').write_text(license_output.stdout + license_output.stderr, encoding='utf-8')
    shutil.copyfile(BASE / 'THIRD_PARTY.md', licenses / 'THIRD_PARTY.md')
    arguments = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--onefile', '--console',
                 '--name', 'media-backend', '--distpath', str(resources), '--workpath', str(BASE / 'build' / 'backend'),
                 '--specpath', str(BASE / 'build'), '--collect-all', 'imageio_ffmpeg', '--collect-all', 'pillow_heif',
                 '--hidden-import', 'PIL.Image', '--hidden-import', 'PIL.ImageOps', '--hidden-import', 'tkinter',
                 '--hidden-import', 'tkinter.filedialog']
    arguments.extend(['--add-data', str(licenses) + ':licenses'])
    for resource in sorted([*BASE.glob('*.html'), *BASE.glob('*.js'), BASE / 'portable_image_probe.py', BASE / 'portable_video_cover.py']):
        arguments.extend(['--add-data', str(resource) + ':.'])
    if sys.platform == 'darwin':
        native = BASE / 'native' / 'trash_media'
        subprocess.run(['swiftc', str(native.with_suffix('.swift')), '-o', str(native)], check=True)
        arguments.extend(['--add-binary', str(native) + ':native'])
    subprocess.run([*arguments, str(BASE / 'desktop_backend.py')], check=True)
    executable = resources / ('media-backend.exe' if sys.platform == 'win32' else 'media-backend')
    subprocess.run([str(executable), '--smoke-test'], check=True)
    shell = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--onedir', '--windowed',
             '--name', 'MediaOrganizer', '--distpath', str(desktop), '--workpath', str(BASE / 'build' / 'shell'),
             '--specpath', str(BASE / 'build'), '--add-data', str(licenses) + ':licenses', '--add-binary', str(executable) + ':native']
    if sys.platform == 'darwin':
        shell.extend(['--osx-bundle-identifier', 'local.mediaorganizer.desktop'])
    subprocess.run([*shell, str(BASE / 'desktop_entry.py')], check=True)
    print('Desktop application: ' + str(desktop), flush=True)


if __name__ == '__main__':
    run()
