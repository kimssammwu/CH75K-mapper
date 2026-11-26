# 제3자 고지

3D 모델을 생성하는 `ui/anatomy/`의 네 모듈은 Ryan의 Anatomy 프로젝트에서 가져왔습니다. MIT 라이선스와 저작권 고지는 [ui/anatomy/LICENSE](ui/anatomy/LICENSE)에 있습니다. [Anatomy 업스트림](https://github.com/wheresryan22/anatomy)

실행 패키지는 PyInstaller로 빌드합니다. PyInstaller 부트로더의 배포 예외는 포함된 Python과 라이브러리의 라이선스를 변경하지 않습니다. 빌드가 발견한 Python 런타임 고지는 패키지의 `licenses/python/`에, Debian 계열에서 찾은 공유 라이브러리 고지는 `licenses/linux/`에 포함됩니다. 실행 패키지를 공개 배포하기 전 대상 빌드에 들어간 라이브러리와 고지를 확인합니다. [PyInstaller 라이선스](https://pyinstaller.org/en/stable/license.html) · [Python 라이선스](https://docs.python.org/3/license.html)

Windows 패키지는 BSD 3-Clause 라이선스의 pywebview와 Windows 바인딩을 포함합니다. 설치된 Python 패키지가 제공하는 고지는 `licenses/windows/`에 들어 있습니다. pywebview에는 Microsoft WebView2 SDK의 관리형 바인딩과 로더가 포함되어 있으며, 실행에는 시스템의 WebView2 Evergreen Runtime이 필요합니다. [pywebview 라이선스](https://github.com/r0x0r/pywebview/blob/6.2.1/LICENSE) · [Microsoft WebView2 배포 문서](https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/distribution#files-to-ship-with-the-app)
