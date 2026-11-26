# CH75K Studio

<img src="assets/app-icon.svg" width="80" height="80" alt="CH75K 로고">

아이노트 CH75K와 호환되는 데스크톱 설정 도구입니다. Windows, macOS, Linux에서 앱에 내장된 웹뷰가 같은 3D 편집기를 표시합니다. 편집기는 Python과 직접 메시지를 주고받으므로 로컬 서버나 포트가 필요 없습니다. Windows HID, macOS IOKit, Linux `hidraw`로 키패드의 내장 설정을 읽고 씁니다.

## Quick Start

### macOS에서 직접 빌드하기

Mac에서 Git과 Python 3.12를 준비합니다. Apple Silicon에서는 Rosetta를 사용하지 않는 터미널에서 빌드합니다. 빌드 결과는 실행 중인 Python의 아키텍처를 따릅니다. [Python 공식 다운로드](https://www.python.org/downloads/macos/) · [PyInstaller macOS 빌드 안내](https://pyinstaller.org/en/stable/feature-notes.html#macos-multi-arch-support)

```sh
git clone --depth 1 --branch v0.1.2 https://github.com/kimssammwu/CH75K-mapper.git
cd CH75K-mapper
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-build.txt
.venv/bin/python build.py --output dist
open dist/CH75K.app
```

로컬 빌드에는 PyInstaller의 기본 ad hoc 서명이 적용되므로 Developer ID 인증서가 필요 없습니다. 웹에서 내려받는 앱이 Gatekeeper의 일반 검사를 통과하도록 배포하려면 Developer ID로 서명하고 Apple 공증을 받습니다. [PyInstaller 코드 서명 안내](https://pyinstaller.org/en/stable/feature-notes.html#macos-binary-code-signing) · [Apple 공증 안내](https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution)

## GitHub Actions로 빌드하고 다운로드

[GitHub Releases](https://github.com/kimssammwu/CH75K-mapper/releases)에서 Windows 또는 Linux 실행 파일과 `SHA256SUMS`를 내려받습니다. 버전 태그를 올리면 GitHub Actions가 실행 테스트를 거친 뒤 두 파일과 체크섬을 게시합니다.

`main`에 올린 변경은 같은 빌드 검사로 확인합니다. Windows와 Linux의 CI 빌드를 직접 만들려면 **Actions → Build CH75K → Run workflow**에서 실행하고, 완료된 실행의 **Artifacts**에서 내려받습니다. [GitHub 워크플로 수동 실행 안내](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow)

| 대상 | 다운로드 파일 |
| --- | --- |
| Linux x86_64 | `CH75K-Linux-x86_64` |
| Windows x86_64 | `CH75K-Windows-x86_64.exe` |

### 다운로드 검증

GitHub CLI로 `SHA256SUMS`의 서명과 빌드 출처를 확인합니다. `v0.1.2`를 내려받은 릴리스의 태그에 맞춥니다. 같은 명령에서 `SHA256SUMS` 대신 실행 파일 이름을 지정해 직접 검증할 수도 있습니다. [GitHub 증명 검증 안내](https://cli.github.com/manual/gh_attestation_verify)

```sh
gh attestation verify SHA256SUMS --repo kimssammwu/CH75K-mapper --signer-workflow kimssammwu/CH75K-mapper/.github/workflows/build.yml --source-ref refs/tags/v0.1.2 --deny-self-hosted-runners
```

실행 파일의 SHA-256을 계산해 `SHA256SUMS`에서 같은 파일 이름의 값과 비교합니다.

- Linux는 `sha256sum CH75K-Linux-x86_64`를 실행합니다.
- Windows PowerShell에서는 `Get-FileHash -Algorithm SHA256 .\CH75K-Windows-x86_64.exe`로 확인합니다.

## 실행

- **Linux**는 다운로드한 파일에 `chmod +x CH75K-Linux-x86_64`를 실행하고, 같은 폴더에서 `./CH75K-Linux-x86_64`로 엽니다. 첫 실행 때 현재 사용자용 앱 항목과 아이콘이 등록됩니다. 파일을 옮긴 뒤 새 위치에서 한 번 실행하면 등록 경로도 갱신됩니다.
- **macOS**는 [Quick Start](#quick-start)에서 빌드한 `dist/CH75K.app`을 실행합니다.
- **Windows**는 다운로드한 `CH75K-Windows-x86_64.exe`를 엽니다. Windows 10 또는 11 x64와 Microsoft Edge WebView2 Runtime이 필요합니다. [Microsoft WebView2 Runtime](https://developer.microsoft.com/microsoft-edge/webview2/#download)
- **소스**는 Python 3.10 이상에서 `python3 desktop_app.py`로 실행합니다.

실행하면 **CH75K Studio** 창이 열립니다. 확인을 마치면 창을 닫거나 화면 상단의 **종료**를 누릅니다.

Linux는 시스템의 GTK 3와 WebKitGTK 4.1 또는 4.0을 사용합니다. Ubuntu에서 런타임이 없다고 표시되면 `sudo apt install libwebkit2gtk-4.1-0`으로 설치할 수 있습니다. [Ubuntu WebKitGTK 패키지](https://packages.ubuntu.com/jammy/libs/libwebkit2gtk-4.1-0)

Linux 단일 파일은 실행할 때 내부 파일을 실행 가능한 임시 디렉터리에 풉니다. [PyInstaller 단일 파일 동작](https://pyinstaller.org/en/stable/operating-mode.html#bundling-to-one-file)

## Linux·Windows 로컬 빌드

실행하려는 OS에서 빌드하고, GitHub Actions와 동일한 Python 3.12를 사용합니다. Linux에서는 다음 명령을 실행합니다.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-build.txt
.venv/bin/python build.py --output dist --standalone
```

Windows에서는 PowerShell로 실행합니다.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt -r requirements-windows.txt
.\.venv\Scripts\python.exe build.py --output dist --standalone
```

Linux와 Windows는 `dist/`에 단일 실행 파일을 생성합니다. 압축 패키지가 필요한 경우 `--archive`로 빌드합니다.

앱 빌드는 포함된 `mapper.html`을 사용합니다. 3D 화면을 수정한 뒤 다시 만들려면 Node.js 20 이상에서 `node ui/build.mjs`를 실행합니다. [공개 범위와 라이선스 검토](PUBLICATION.md) · [제3자 고지](THIRD_PARTY_NOTICES.md)

## 입체 키 매핑

모델에서 키캡을 눌러 동작을 선택합니다. **키 감지**를 누른 뒤 원하는 키나 조합을 누르고 모두 놓으면 입력칸에 들어갑니다. **매크로**에서는 **다음 키 감지**로 키를 더하고, **딜레이 추가**로 별도 대기 단계를 넣습니다. Esc와 Tab은 감지 중 취소와 탐색에 사용하므로 매핑할 때는 입력칸에 직접 적습니다. 마우스 버튼, 상대 이동, 수직 휠도 매크로에 넣을 수 있습니다.

여섯 개 키에 키 입력, 순차 매크로, 미디어·시스템 기능을 지정할 수 있습니다. 이전 버전의 JSON을 열면 여기에서 설정할 수 있는 키 동작만 가져옵니다.

편집 중인 프로필은 이 PC에 보관되며, **프로필 저장**을 누르면 원하는 위치에 JSON 파일을 저장할 수 있습니다. 브라우저에서 편집하던 프로필은 JSON으로 내보낸 뒤 앱의 **불러오기**에서 열 수 있습니다. **장치에 적용**은 CH75K의 내장 설정 메모리에 기록합니다. 장치에 기록한 키 설정은 매퍼를 종료하거나 다른 호환 PC에 연결해도 사용할 수 있습니다. [제조사 내장 메모리 안내](https://fusionfnc1.cafe24.com/article/ch75k/13/277/)

다이얼의 **볼륨 조절**과 휠 아래의 **HDMI 화면 켜기·끄기**는 고정 기능입니다. 모델에서 조작부를 선택하면 설명이 나옵니다. 화면 버튼은 허브의 HDMI와 DP Alt Mode를 지원하는 USB-C 연결을 사용합니다. [제조사 기본 버튼 안내](https://fusionfnc1.cafe24.com/article/ch75k/13/274/)

키보드 없이도 프로필을 편집하고 미리 볼 수 있습니다. 새 USB 장치가 감지되면 CH75K를 연결한 것이 맞는지 팝업에서 **확인**하거나 **거절**합니다. 거절한 장치를 다시 확인하거나 연결 오류에서 복구하려면 오른쪽 위의 **장치 새로고침**을 누릅니다.

장치를 확인하면 앱이 저장된 설정을 읽어 편집값과 비교합니다. **장치에서 읽기**는 읽을 수 있는 장치 설정을 편집기에 불러옵니다. **입력 지우기**를 누르면 선택한 키의 기본 동작이 편집값으로 지정됩니다. **장치에 적용**은 장치와 다른 편집값이 있을 때 활성화되며, 누르면 지정한 키의 변경을 바로 저장합니다. 앱은 기존 값을 백업하고 기록된 값을 다시 읽어 확인한 뒤 키 입력 모드로 돌아가는 명령을 보냅니다.

저장한 뒤 키 입력이 멈춰 있으면 오른쪽 위 **장치 새로고침**을 누릅니다. 확인된 CH75K의 저장값을 읽고 키 입력 모드 복귀 명령을 다시 보냅니다.

버튼에는 `F13`, `Ctrl+C`, `Ctrl+K, @120, Ctrl+C`처럼 입력합니다. 쉼표는 동작을 잇고, `@120`은 대기 시간을 밀리초로 지정합니다. `Ctrl+K@120`처럼 키를 놓은 뒤의 대기 시간도 지정할 수 있습니다. 실제 저장은 제조사 앱처럼 약 8ms 단위입니다.

반복 매크로와 미디어·시스템 프로필은 입체 매핑 화면과 터미널에서 적용할 수 있습니다.

## USB 접근 권한

macOS와 Windows에서는 앱을 일반 사용자로 실행합니다. Windows에서 장치를 열 수 없으면 CH75K를 열어 둔 다른 설정 도구를 종료하고 다시 시도하세요.

### Linux

새 장치 팝업에서 **확인**을 누릅니다. USB에 접근할 권한이 부족하면 앱이 시스템 인증 창을 엽니다. 승인하면 CH75K용 udev 규칙을 등록하고 원래 작업을 한 번 다시 시도합니다.

인증 창을 사용할 수 없는 Linux 환경에서는 [udev 규칙](70-ch75k-native.rules)을 내려받습니다. 그 파일이 있는 폴더에서 관리자 권한으로 규칙을 등록한 뒤 CH75K를 다시 연결합니다.

```sh
sudo install -m 0644 70-ch75k-native.rules /etc/udev/rules.d/70-ch75k-native.rules
sudo udevadm control --reload-rules
```

## 터미널 사용

```sh
python3 ch75k.py devices
python3 ch75k.py preview profile.json
python3 ch75k.py read
python3 ch75k.py apply profile.json --confirm-ch75k
```

장치가 여러 개면 `read`나 `apply`에 `--device`와 함께 `devices`가 표시한 식별자를 지정합니다. JSON 예시는 다음과 같습니다.

```json
{
  "keys": {
    "top-left": {"type": "media", "action": "play-pause"},
    "top-right": {"type": "system", "action": "sleep"},
    "bottom-left": "F13",
    "bottom-center": {
      "type": "macro",
      "sequence": "Ctrl+C@120, MouseMove(24 -12), MouseLeft, MouseWheel(-2)",
      "repeat": "count",
      "count": 3
    }
  }
}
```

반복 값은 `count`(1~65535회), `hold`(누르는 동안), `toggle`(다시 누를 때까지)입니다. `count` 필드는 횟수 지정 방식에서만 입력합니다. 클릭은 `MouseLeft`, `MouseRight`, `MouseMiddle`로 넣습니다. `MouseMove(x y)`는 오른쪽과 아래쪽이 양수인 상대 이동이고, `MouseWheel(n)`은 양수가 위쪽인 스크롤입니다. 각 단계 뒤에 `@120`처럼 대기 시간을 붙일 수 있습니다.

## 검증 범위

장치의 HID Report 7 길이와 인터페이스 식별 정보를 확인한 뒤 통신합니다. [Linux hidraw 문서](https://docs.kernel.org/hid/hidraw.html) · [Apple IOKit 문서](https://developer.apple.com/documentation/iokit/1588659-iohiddevicegetreport) · [Windows HID 문서](https://learn.microsoft.com/en-us/windows-hardware/drivers/ddi/hidsdi/nf-hidsdi-hidd_getfeature)

USB ID를 다른 모델도 사용하므로, 앱은 제조사 CH75K의 설정 헤더와 6버튼 매트릭스가 맞는 장치에만 씁니다. 알려진 프로필이 없는 초기 장치나 다른 펌웨어는 설정 쓰기가 거부될 수 있습니다. 기존 매크로를 보존할 빈 공간을 확인하지 못한 경우에도 쓰지 않습니다.

GitHub Actions에서 Windows·Linux 실행 파일과 소스에서 빌드한 macOS 앱을 열어 앱 창, 장치 조회, 매크로 미리 보기와 종료를 검사합니다. macOS 빌드에서는 앱 서명도 확인합니다. 실제 키패드에서 동작하는 매크로와 장치 접근은 별도 연결 시험이 필요합니다. 백업은 Linux에서 `${XDG_STATE_HOME:-~/.local/state}/ch75k-native/backups`, macOS에서 `~/Library/Application Support/ch75k-native/backups`, Windows에서 `%LOCALAPPDATA%\ch75k-native\backups`에 남습니다. `XDG_STATE_HOME`을 지정하면 백업 루트가 바뀝니다.
