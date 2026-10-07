"""Create a drag-to-Applications DMG without editing the user's installation."""
from pathlib import Path
import platform
import shutil
import subprocess

base = Path(__file__).resolve().parent.parent
stage = base / 'build' / 'dmg'
stage.mkdir(parents=True, exist_ok=True)
shutil.copytree(base / 'dist' / 'desktop' / 'MediaOrganizer.app', stage / 'MediaOrganizer.app', symlinks=True, dirs_exist_ok=True)
applications = stage / 'Applications'
if not applications.exists():
    applications.symlink_to('/Applications', target_is_directory=True)
output = base / 'dist' / 'installers'
output.mkdir(parents=True, exist_ok=True)
subprocess.run(['hdiutil', 'create', '-volname', 'Media Organizer', '-srcfolder', str(stage), '-ov', '-format', 'UDZO',
                str(output / ('MediaOrganizer-macOS-' + platform.machine() + '.dmg'))], check=True)
