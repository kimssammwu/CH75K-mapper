# CH75K 매퍼

아이노트 CH75K와 호환되는 데스크톱 설정 도구입니다. 두 OS에서 앱에 내장된 웹뷰가 같은 3D 편집기를 표시합니다. 편집기는 Python과 직접 메시지를 주고받으므로 로컬 서버나 포트가 필요 없습니다. macOS에서는 IOKit, Linux에서는 `hidraw`로 키패드의 내장 설정을 읽고 씁니다.

## GitHub Actions로 빌드하고 다운로드

1. 소스 ZIP을 풀고 `CH75K-Mapper/` 안의 파일을 GitHub 저장소 기본 브랜치에 둡니다. 숨김 폴더인 `.github/`도 포함합니다.
2. 저장소 쓰기 권한으로 **Actions → Build CH75K → Run workflow**에서 빌드를 실행합니다.
3. 완료된 실행의 **Artifacts**에서 원하는 OS의 패키지를 다운로드합니다. [GitHub 워크플로 수동 실행 안내](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow)

| 대상 | 다운로드 파일 |
| --- | --- |
| Linux x86_64 | `CH75K-Linux-x86_64-portable.tar.gz` |
| macOS Apple Silicon | `CH75K-macOS-arm64.zip` |
| macOS Intel | `CH75K-macOS-x86_64.zip` |

## 실행

- **Linux**는 tar.gz에서 `CH75K`를 꺼내 그 파일만 실행하면 됩니다. 파일을 다른 폴더에 옮겨도 동작합니다. 권한 규칙과 사용 안내도 압축에 들어 있습니다.
- **macOS**는 ZIP을 풀고 `CH75K.app`을 엽니다. 배포용 Developer ID 서명과 Apple 공증은 별도 단계입니다. [Apple 공증 안내](https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution)
- **소스**는 Python 3.10 이상에서 `python3 desktop_app.py`로 실행합니다.

실행하면 **CH75K 매퍼** 창이 열립니다. 확인을 마치면 창을 닫거나 화면 상단의 **종료**를 누릅니다.

Linux는 시스템의 GTK 3와 WebKitGTK 4.1 또는 4.0을 사용합니다. Ubuntu에서 런타임이 없다고 표시되면 `sudo apt install libwebkit2gtk-4.1-0`으로 설치할 수 있습니다. [Ubuntu WebKitGTK 패키지](https://packages.ubuntu.com/jammy/libs/libwebkit2gtk-4.1-0)

Linux 단일 파일은 실행할 때 내부 파일을 실행 가능한 임시 디렉터리에 풉니다. [PyInstaller 단일 파일 동작](https://pyinstaller.org/en/stable/operating-mode.html#bundling-to-one-file)

## 로컬 빌드

두 OS에서 같은 명령을 사용합니다. 실행하려는 OS에서 빌드하고, GitHub Actions와 동일한 Python 3.12를 사용합니다.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-build.txt
.venv/bin/python build.py --output dist --archive
```

Linux는 `dist/CH75K-Linux-<아키텍처>` 단일 실행 파일과 `-portable.tar.gz`를 생성합니다. macOS는 `dist/CH75K.app`과 ZIP을 생성하며, 기본 아키텍처는 빌드하는 Python을 따릅니다.

앱 빌드는 포함된 `mapper.html`을 사용합니다. 3D 화면을 수정한 뒤 다시 만들려면 Node.js 20 이상에서 `node ui/build.mjs`를 실행합니다. [공개 범위와 라이선스 검토](PUBLICATION.md) · [제3자 고지](THIRD_PARTY_NOTICES.md)

## 입체 키 매핑

모델에서 키캡을 눌러 동작을 선택합니다. **키 감지**를 누른 뒤 원하는 키나 조합을 누르고 모두 놓으면 입력칸에 들어갑니다. **매크로**에서는 **다음 키 감지**로 키를 더하고, **딜레이 추가**로 별도 대기 단계를 넣습니다. Esc와 Tab은 감지 중 취소와 탐색에 사용하므로 매핑할 때는 입력칸에 직접 적습니다. 마우스 버튼, 상대 이동, 수직 휠도 매크로에 넣을 수 있습니다.

여섯 개 키에 키 입력, 순차 매크로, 미디어·시스템 기능을 지정할 수 있습니다. 이전 버전의 JSON을 열면 여기에서 설정할 수 있는 키 동작만 가져옵니다.

편집 중인 프로필은 이 PC에 보관되며, **프로필 저장**을 누르면 원하는 위치에 JSON 파일을 저장할 수 있습니다. 브라우저에서 편집하던 프로필은 JSON으로 내보낸 뒤 앱의 **불러오기**에서 열 수 있습니다. **장치에 적용**은 CH75K의 내장 설정 메모리에 기록합니다. 장치에 기록한 키 설정은 매퍼를 종료하거나 다른 호환 PC에 연결해도 사용할 수 있습니다. [제조사 내장 메모리 안내](https://fusionfnc1.cafe24.com/article/ch75k/13/277/)

다이얼의 **볼륨 조절**과 휠 아래의 **HDMI 화면 켜기·끄기**는 고정 기능입니다. 모델에서 조작부를 선택하면 설명이 나옵니다. 화면 버튼은 허브의 HDMI와 DP Alt Mode를 지원하는 USB-C 연결을 사용합니다. [제조사 기본 버튼 안내](https://fusionfnc1.cafe24.com/article/ch75k/13/274/)

키보드 없이도 프로필을 편집하고 미리 볼 수 있습니다. 연결된 키보드의 설정을 읽거나 적용하려면 앱에서 USB 장치를 선택하고 모델을 확인합니다.

버튼에는 `F13`, `Ctrl+C`, `Ctrl+K, @120, Ctrl+C`처럼 입력합니다. 쉼표는 동작을 잇고, `@120`은 대기 시간을 밀리초로 지정합니다. `Ctrl+K@120`처럼 키를 놓은 뒤의 대기 시간도 지정할 수 있습니다. 실제 저장은 제조사 앱처럼 약 8ms 단위입니다.

반복 매크로와 미디어·시스템 프로필은 입체 매핑 화면과 터미널에서 적용할 수 있습니다.

## USB 접근 권한

macOS에서는 앱을 일반 사용자로 실행합니다. 시스템에서 HID 장치 접근을 거부하면 macOS가 제공한 권한 안내를 확인하세요.

### Linux

일반 사용자에게 접근 권한이 없다고 표시되면, 이 폴더에서 Ubuntu의 관리자 권한으로 규칙을 한 번 등록한 뒤 USB를 다시 연결합니다.

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

장치가 여러 개면 `read`나 `apply`에 `--device /dev/hidrawN`을 추가합니다. JSON 예시는 다음과 같습니다.

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

장치의 기능을 대조한 뒤, 두 OS에서 HID Report 7의 길이와 장치 식별 정보를 실제 디스크립터에서 확인하도록 구현했습니다. [Linux hidraw 문서](https://docs.kernel.org/hid/hidraw.html) · [Apple IOKit 문서](https://developer.apple.com/documentation/iokit/1588659-iohiddevicegetreport)

USB ID를 다른 모델도 사용하므로, 앱은 제조사 CH75K의 설정 헤더와 6버튼 매트릭스가 맞는 장치에만 씁니다. 알려진 프로필이 없는 초기 장치나 다른 펌웨어는 설정 쓰기가 거부될 수 있습니다. 기존 매크로를 보존할 빈 공간을 확인하지 못한 경우에도 쓰지 않습니다.

GitHub Actions에서 패키지를 풀어 앱 창, 장치 조회, 매크로 미리 보기와 종료를 검사합니다. 실제 키패드에서 동작하는 매크로와 장치 접근은 별도 연결 시험이 필요합니다. 백업은 Linux에서 `${XDG_STATE_HOME:-~/.local/state}/ch75k-native/backups`, macOS에서 `~/Library/Application Support/ch75k-native/backups`에 남습니다. `XDG_STATE_HOME`을 지정하면 백업 루트가 바뀝니다.
