"""Verify release contents and exercise the local CH75K WebView without writing USB."""

from __future__ import annotations

import argparse
from contextlib import suppress
import os
from pathlib import Path
import plistlib
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
from zipfile import ZipFile


RELEASE_FILES = (
    "README.md", "PUBLICATION.md", "THIRD_PARTY_NOTICES.md",
    "ui/anatomy/LICENSE", "assets/app-icon.svg",
)
WINDOWS_NOTICE_FILES = (
    "licenses/windows/pywebview/licenses/LICENSE", "licenses/windows/proxy-tools/LICENSE.txt",
    "licenses/windows/webview2-sdk/LICENSE.txt", "licenses/windows/webview2-sdk/NOTICE.txt",
)
PYTHON_LICENSES = ("LICENSE.txt", "copyright")
SUCCESS_MARKER = "CH75K_PORTLESS_SMOKE_OK"


def smoke(executable: Path, *, cwd: Path | None = None):
    executable = executable.resolve(strict=True)
    command = [str(executable), "--smoke-test"]
    if executable.suffix == ".py":
        command.insert(0, sys.executable)

    windows = os.name == "nt"
    with tempfile.TemporaryDirectory(prefix="ch75k-desktop-smoke-", ignore_cleanup_errors=windows) as temporary_directory:
        workspace = Path(temporary_directory)
        result = workspace / "result.txt"
        diagnostics = result.with_suffix(".log")
        if windows:
            command.extend(("--smoke-result", str(result)))
        env = os.environ.copy()
        for variable, name in (
            ("HOME", "home"),
            ("XDG_CACHE_HOME", "cache"),
            ("XDG_CONFIG_HOME", "config"),
            ("XDG_DATA_HOME", "data"),
            ("XDG_STATE_HOME", "state"),
            ("TMPDIR", "tmp"),
        ):
            directory = workspace / name
            directory.mkdir()
            env[variable] = str(directory)
        if windows:
            env.update(TMP=env["TMPDIR"], TEMP=env["TMPDIR"], LOCALAPPDATA=env["XDG_DATA_HOME"])

        with subprocess.Popen(
            command, cwd=cwd, env=env, start_new_session=not windows, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, encoding="utf-8", errors="replace",
        ) as process:
            try:
                output, _ = process.communicate(timeout=90 if windows else 45)
            except subprocess.TimeoutExpired as exc:
                # Killing the bootloader alone could leave the onefile child running.
                if windows:
                    taskkill = Path(os.environ["SystemRoot"]) / "System32" / "taskkill.exe"
                    with suppress(OSError, subprocess.TimeoutExpired):
                        subprocess.run([str(taskkill), "/F", "/T", "/PID", str(process.pid)],
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=8)
                    if process.poll() is None:
                        process.kill()
                else:
                    with suppress(ProcessLookupError):
                        os.killpg(process.pid, signal.SIGKILL)
                output, _ = process.communicate()
                log = diagnostics.read_text(encoding="utf-8", errors="replace") if diagnostics.is_file() else ""
                raise RuntimeError(f"CH75K window test timed out.\n{output}\n{log}") from exc
            status = result.read_text(encoding="utf-8") if windows and result.is_file() else output
            if process.returncode or SUCCESS_MARKER not in status.splitlines():
                log = diagnostics.read_text(encoding="utf-8", errors="replace") if diagnostics.is_file() else ""
                raise RuntimeError(f"CH75K window test failed ({process.returncode}).\n{status}\n{log}")


def check_release_files(executable: Path, documentation: Path, required_files: tuple[str, ...]):
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise RuntimeError(f"Missing or non-executable file in archive: {executable}")
    for name in required_files:
        path = documentation / name
        if not path.is_file() or path.stat().st_size == 0:
            raise RuntimeError(f"Missing or empty release document: {path}")
    python_licenses = (documentation / "licenses" / "python" / name for name in PYTHON_LICENSES)
    if not any(path.is_file() and path.stat().st_size > 0 for path in python_licenses):
        raise RuntimeError(f"Missing Python runtime license under {documentation / 'licenses' / 'python'}")


def smoke_isolated_executable(executable: Path):
    with tempfile.TemporaryDirectory(prefix="ch75k-onefile-check-") as folder:
        workspace = Path(folder)
        isolated = workspace / "isolated"
        isolated.mkdir()
        standalone = isolated / executable.name
        shutil.copy2(executable, standalone)
        smoke(standalone, cwd=workspace)


def check_macos_app(application: Path) -> Path:
    executable = application / "Contents/MacOS/CH75K"
    check_release_files(executable, application / "Contents/Resources",
                        (*RELEASE_FILES, "mapper.html", "app-icon.icns", "assets/app-icon.png"))
    with (application / "Contents/Info.plist").open("rb") as info:
        bundle = plistlib.load(info)
    if bundle.get("NSAppTransportSecurity"):
        raise RuntimeError("CH75K.app contains a network security exception.")
    if bundle.get("CFBundleIconFile") != "app-icon.icns":
        raise RuntimeError("CH75K.app does not use the packaged application icon.")
    subprocess.run(["/usr/bin/codesign", "--verify", "--deep", "--strict", str(application)], check=True)
    return executable


def smoke_disk_image(image: Path):
    architecture = image.stem.removeprefix("CH75K-macOS-")
    expected = {"arm64", "x86_64"} if architecture == "universal2" else {architecture}
    hdiutil = "/usr/bin/hdiutil"
    subprocess.run([hdiutil, "verify", str(image)], check=True)
    with tempfile.TemporaryDirectory(prefix="ch75k-disk-image-check-") as folder:
        mount = Path(folder) / "mounted"
        mount.mkdir()
        subprocess.run([hdiutil, "attach", "-readonly", "-nobrowse", "-noautoopen",
                        "-mountpoint", str(mount), str(image)], check=True)
        try:
            executable = check_macos_app(mount / "CH75K.app")
            architectures = subprocess.check_output(
                ["/usr/bin/lipo", "-archs", str(executable)], text=True).split()
            if set(architectures) != expected:
                raise RuntimeError(f"Expected {sorted(expected)}, found {architectures} in {image.name}.")
            smoke(executable, cwd=Path(folder))
        finally:
            if subprocess.run([hdiutil, "detach", str(mount)]).returncode:
                subprocess.run([hdiutil, "detach", "-force", str(mount)], check=True)


def smoke_standalone(artifact: Path):
    artifact = artifact.resolve(strict=True)
    if artifact.suffix == ".dmg":
        smoke_disk_image(artifact)
        return

    from PyInstaller.archive.readers import CArchiveReader

    required = (
        (*RELEASE_FILES, *WINDOWS_NOTICE_FILES, "assets/app-icon.ico")
        if artifact.suffix == ".exe"
        else (*RELEASE_FILES, "70-ch75k-native.rules", "assets/app-icon.png")
    )
    archive = CArchiveReader(str(artifact))
    names = {name.replace("\\", "/"): name for name in archive.toc}
    for name in (*required, "mapper.html"):
        if name not in names or not archive.extract(names[name]):
            raise RuntimeError(f"Missing or empty embedded release file {name} in {artifact.name}.")
    if not any((key := f"licenses/python/{name}") in names and archive.extract(names[key])
               for name in PYTHON_LICENSES):
        raise RuntimeError(f"Missing embedded Python runtime license in {artifact.name}.")
    smoke_isolated_executable(artifact)


def smoke_archive(archive: Path):
    archive = archive.resolve(strict=True)
    with tempfile.TemporaryDirectory(prefix="ch75k-package-check-") as temporary_directory:
        workspace = Path(temporary_directory)
        extracted = workspace / "extracted"
        extracted.mkdir()
        if archive.name.startswith("CH75K-Windows-") and archive.name.endswith("-portable.zip"):
            with ZipFile(archive) as contents:
                contents.extractall(extracted)
            package = extracted / archive.name.removesuffix("-portable.zip")
            executable = package / "CH75K.exe"
            check_release_files(executable, package, (*RELEASE_FILES, *WINDOWS_NOTICE_FILES))
            other_locations = [package / name for name in ("_internal", "mapper.html")]
            if any(path.exists() for path in other_locations):
                raise RuntimeError("The Windows package must bundle its dependencies and mapper inside CH75K.exe.")
            smoke_isolated_executable(executable)
        elif archive.name.endswith("-portable.tar.gz"):
            with tarfile.open(archive, "r:gz") as contents:
                contents.extractall(extracted, filter="data")
            package = extracted / archive.name.removesuffix("-portable.tar.gz")
            executable = package / "CH75K"
            check_release_files(executable, package, (*RELEASE_FILES, "70-ch75k-native.rules"))
            if (package / "_internal").exists():
                raise RuntimeError("The Linux package contains a PyInstaller dependency directory.")
            if (package / "mapper.html").exists():
                raise RuntimeError("The Linux package must bundle mapper.html inside the executable.")
            smoke_isolated_executable(executable)
        elif archive.suffix == ".zip":
            subprocess.run(["/usr/bin/ditto", "-x", "-k", str(archive), str(extracted)], check=True)
            smoke(check_macos_app(extracted / "CH75K.app"))
        else:
            raise ValueError(f"Unsupported release archive: {archive.name}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("executable", nargs="?", type=Path)
    target.add_argument("--archive", type=Path)
    target.add_argument("--standalone", type=Path, help="Verify a published executable or macOS disk image.")
    parser.add_argument("--desktop", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.archive:
        smoke_archive(args.archive)
    elif args.standalone:
        smoke_standalone(args.standalone)
    else:
        smoke(args.executable)
    print("CH75K local WebView smoke test passed.")


if __name__ == "__main__":
    main()
