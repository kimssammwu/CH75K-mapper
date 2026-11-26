#!/usr/bin/env python3
"""Build the CH75K mapper on the operating system it will run on."""

from __future__ import annotations

import argparse
import ast
from importlib.metadata import distribution
import importlib.util
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import sysconfig
import tarfile
import tempfile
from zipfile import ZIP_DEFLATED, ZipFile


SOURCE = Path(__file__).resolve().parent
APP_NAME = "CH75K"
BUNDLE_ID = "local.ch75k.mapper"
MAC_ARCHITECTURES = ("arm64", "x86_64", "universal2")
RELEASE_DOCUMENTS = ("README.md", "PUBLICATION.md", "LICENSE", "NOTICE",
                     "THIRD_PARTY_NOTICES.md", "ui/anatomy/LICENSE")
WINDOWS_BINDINGS = ("pywebview", "pythonnet", "clr-loader", "cffi", "pycparser",
                    "bottle", "proxy-tools", "typing-extensions")


def release_documents() -> list[tuple[Path, Path]]:
    documents = [(SOURCE / name, Path(name)) for name in RELEASE_DOCUMENTS if (SOURCE / name).is_file()]
    python_version = f"{sys.version_info.major}.{sys.version_info.minor}"
    locations = (
        Path(sys.base_prefix) / "LICENSE.txt",
        Path(sysconfig.get_path("stdlib")) / "LICENSE.txt",
        Path(f"/usr/share/doc/python{python_version}/copyright"),
    )
    license_file = next((path for path in locations if path.is_file()), None)
    if license_file is not None:
        documents.append((license_file, Path("licenses/python") / license_file.name))
    return documents


def windows_binding_notices() -> list[tuple[Path, Path]]:
    notices = []
    for package in WINDOWS_BINDINGS:
        installed = distribution(package)
        for resource in installed.files or ():
            if (resource.parts[0].endswith(".dist-info")
                    and resource.name.lower().startswith(("license", "copying", "notice"))):
                source = Path(installed.locate_file(resource))
                target = Path("licenses", "windows", package, *resource.parts[1:])
                if source.is_file():
                    notices.append((source, target))
    return notices


def copy_linux_library_notices(analysis_toc: Path, bundle: Path):
    """Collect available Debian copyright files for bundled native libraries."""
    package_query = shutil.which("dpkg-query")
    if not package_query:
        return
    from PyInstaller.building.build_main import Analysis

    # The pinned PyInstaller saves the original library paths in its analysis TOC.
    entries = ast.literal_eval(analysis_toc.read_text(encoding="utf-8"))
    analysis = dict(zip((name for name, _ in Analysis._GUTS), entries, strict=True))
    libraries = set()
    for _, source, kind in analysis["binaries"]:
        if kind not in ("BINARY", "EXTENSION") or not re.search(r"\.so(?:\.|$)", Path(source).name):
            continue
        resolved = Path(source).resolve()
        for path in (source, str(resolved)):
            libraries.add(path)
            # Debian can record either spelling on a merged /usr filesystem.
            if re.match(r"^/(?:usr/)?lib(?:64)?/", path):
                alias = path.removeprefix("/usr") if path.startswith("/usr/") else "/usr" + path
                if Path(alias).resolve() == resolved:
                    libraries.add(alias)

    packages = set()
    if libraries:
        result = subprocess.run([package_query, "-S", *sorted(libraries)],
                                capture_output=True, text=True, check=False)
        for line in result.stdout.splitlines():
            owners, separator, owned_path = line.partition(": ")
            if separator and owned_path in libraries:
                packages.update(owner.split(":", 1)[0] for owner in owners.split(", "))
    common = Path("/usr/share/common-licenses")
    for package in sorted(packages):
        if not re.fullmatch(r"[a-z0-9][a-z0-9.+-]*", package):
            continue
        notice = Path("/usr/share/doc") / package / "copyright"
        if not notice.is_file():
            continue
        target = bundle / "licenses" / "linux" / package
        target.mkdir(parents=True, exist_ok=True)
        shutil.copy2(notice, target / "copyright")
        for name in set(re.findall(r"/usr/share/common-licenses/([A-Za-z0-9.+-]+)",
                                   notice.read_text(encoding="utf-8", errors="replace"))):
            if (common / name).is_file():
                shutil.copy2(common / name, target / name)


def build_args(stage: Path, system: str, target_arch: str | None,
               signing_identity: str | None, documents: list[tuple[Path, Path]]) -> list[str]:
    is_mac = system == "Darwin"
    command = [
        sys.executable, "-m", "PyInstaller",
        "--name", APP_NAME,
        "--onedir" if is_mac else "--onefile",
        "--distpath", str(stage / "dist"),
        "--workpath", str(stage / "work"),
        "--specpath", str(stage),
        "--add-data", f"{SOURCE / 'mapper.html'}:.",
    ]
    if is_mac:
        command.extend(("--windowed", "--osx-bundle-identifier", BUNDLE_ID))
        if target_arch:
            command.extend(("--target-arch", target_arch))
        if signing_identity:
            command.extend(("--codesign-identity", signing_identity))
        # Put notices in the bundle before PyInstaller signs it.
        for path, relative in documents:
            command.extend(("--add-data", f"{path}:{relative.parent.as_posix()}"))
    elif system == "Windows":
        command.extend(("--windowed", "--collect-data", "webview"))
    command.append(str(SOURCE / "desktop_app.py"))
    return command


def build(output: Path, *, archive: bool = False, target_arch: str | None = None,
          signing_identity: str | None = None) -> tuple[Path, ...]:
    system = platform.system()
    if system not in ("Linux", "Darwin", "Windows"):
        raise ValueError("빌드는 Linux, macOS, Windows에서 실행할 수 있습니다.")
    is_mac = system == "Darwin"
    is_windows = system == "Windows"
    if not is_mac and (target_arch or signing_identity):
        raise ValueError("--target-arch와 --signing-identity는 macOS에서 사용하세요.")
    if importlib.util.find_spec("PyInstaller") is None:
        raise ValueError("빌드용 Python 환경에 requirements-build.txt의 PyInstaller를 설치하세요.")
    if is_windows and importlib.util.find_spec("webview") is None:
        raise ValueError("Windows 빌드용 Python 환경에 requirements-windows.txt를 설치하세요.")
    if not (SOURCE / "mapper.html").is_file():
        raise ValueError("빌드할 키 매퍼 화면 mapper.html이 필요합니다.")
    architecture = target_arch or platform.machine()
    if is_windows and architecture.casefold() == "amd64":
        architecture = "x86_64"
    release = f"{APP_NAME}-{'macOS' if is_mac else system}-{architecture}"
    application_name = APP_NAME + (".app" if is_mac else ".exe" if is_windows else "")
    bundle_name = application_name if is_mac else release + (".exe" if is_windows else "")
    archive_name = release + {"Darwin": ".zip", "Linux": "-portable.tar.gz",
                              "Windows": "-portable.zip"}[system]
    targets = [output / bundle_name]
    if archive:
        targets.append(output / archive_name)
    for existing in targets:
        if existing.exists() or existing.is_symlink():
            raise ValueError(f"이미 빌드 결과가 있습니다. 새 출력 폴더를 지정하세요. {existing}")

    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".ch75k-build-", dir=output) as temporary:
        stage = Path(temporary).resolve()
        documents = release_documents()
        subprocess.run(build_args(stage, system, target_arch, signing_identity, documents),
                       cwd=SOURCE, check=True)
        source = stage / "dist" / application_name
        executable = source / "Contents" / "MacOS" / APP_NAME if is_mac else source
        if not executable.is_file():
            raise OSError(f"빌드된 실행 파일을 찾지 못했습니다. {executable}")
        if is_mac:
            subprocess.run(["/usr/bin/codesign", "--verify", "--deep", "--strict", str(source)],
                           check=True)

        if archive:
            bundle_archive = stage / archive_name
            if is_mac:
                subprocess.run(["/usr/bin/ditto", "-c", "-k", "--sequesterRsrc", "--keepParent",
                                str(source), str(bundle_archive)], check=True)
            elif is_windows:
                with ZipFile(bundle_archive, "w", compression=ZIP_DEFLATED) as package:
                    for path, relative in documents + windows_binding_notices():
                        package.write(path, arcname=f"{release}/{relative.as_posix()}")
                    package.write(executable, arcname=f"{release}/{application_name}")
            else:
                contents = stage / "archive"
                linux_rule = Path("70-ch75k-native.rules")
                for path, relative in (*documents, (SOURCE / linux_rule, linux_rule)):
                    destination = contents / relative
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(path, destination)
                copy_linux_library_notices(stage / "work" / APP_NAME / "Analysis-00.toc", contents)
                with tarfile.open(bundle_archive, "w:gz") as package:
                    package.add(contents, arcname=bundle_name)
                    package.add(executable, arcname=f"{bundle_name}/{APP_NAME}")
        source.rename(targets[0])
        if archive:
            bundle_archive.rename(targets[1])
    return tuple(targets)


def main() -> int:
    parser = argparse.ArgumentParser(description="이 OS에서 실행되는 CH75K 매퍼를 빌드합니다.")
    parser.add_argument("--output", type=Path, default=Path("dist"),
                        help="출력 폴더, 기본값 dist")
    parser.add_argument("--archive", action="store_true",
                        help="공유용 ZIP(macOS, Windows) 또는 tar.gz(Linux)도 생성")
    parser.add_argument("--target-arch", choices=MAC_ARCHITECTURES,
                        help="macOS 아키텍처. 생략하면 실행 중인 Python을 따릅니다.")
    parser.add_argument("--signing-identity",
                        help="macOS 코드 서명 ID. 생략하면 임시 서명을 사용합니다.")
    args = parser.parse_args()
    try:
        results = build(args.output.expanduser().resolve(), archive=args.archive,
                        target_arch=args.target_arch, signing_identity=args.signing_identity)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print(error, file=sys.stderr)
        return 1
    for result in results:
        print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
