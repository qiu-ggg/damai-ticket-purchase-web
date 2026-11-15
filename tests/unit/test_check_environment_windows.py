import sys
from pathlib import Path
from types import SimpleNamespace

from damai import check_environment


WINDOWS_CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
WINDOWS_CHROMEDRIVER = r"C:\Tools\chromedriver.exe"


def fake_install_dir(monkeypatch, chrome_path, version):
    """把「Chrome 安装目录」也一起伪造成指定的版本。

    ★ 为什么必须连它一起伪造：
      `_find_chrome()` 现在的顺序是**先读安装目录、再跑 `chrome.exe --version`** ——
      因为 Windows 上 `--version` 根本不打印版本，它会真的把浏览器启动起来然后
      一直不退出，于是每次调用白等满 5 秒超时（实测这一项占了启动流程的 5.09 秒，
      而真正把浏览器拉起来只要 0.68 秒）。

      只伪造 `--version` 的话，被读到的其实是**本机真实的** Chrome 版本，
      于是这些用例会变成「依赖跑测试的机器上装了什么版本」——
      在没装 Chrome 的机器上还会直接失败。
      把安装目录一起伪造，用例才重新变成自洽的。
    """
    app_dir = str(Path(chrome_path).parent)
    ver_dir = str(Path(app_dir) / version)
    real_listdir, real_isdir = check_environment.os.listdir, check_environment.os.path.isdir

    def fake_listdir(p):
        if str(p) == app_dir:
            return [version]
        return real_listdir(p)

    def fake_isdir(p):
        if str(p) == ver_dir:
            return True
        return real_isdir(p)

    monkeypatch.setattr(check_environment.os, "listdir", fake_listdir)
    monkeypatch.setattr(check_environment.os.path, "isdir", fake_isdir)


def test_check_chrome_detects_windows_program_files_install(monkeypatch):
    """Chrome installed in Program Files should be detected on Windows."""

    def fake_exists(path):
        return path == WINDOWS_CHROME

    def fake_version(command):
        if command[0] == WINDOWS_CHROME:
            return "Google Chrome 136.0.7103.114"
        return None

    monkeypatch.setattr(check_environment.os.path, "exists", fake_exists)
    monkeypatch.setattr(check_environment, "_run_command_get_version", fake_version)
    fake_install_dir(monkeypatch, WINDOWS_CHROME, "136.0.7103.114")

    assert check_environment.check_chrome() is True


def test_check_version_match_uses_windows_chrome_and_driver_paths(monkeypatch):
    """Version matching should work for Windows Chrome and chromedriver.exe."""

    def fake_exists(path):
        return path in {WINDOWS_CHROME, WINDOWS_CHROMEDRIVER}

    def fake_islink(path):
        return False

    def fake_version(command):
        if command[0] == WINDOWS_CHROME:
            return "Google Chrome 136.0.7103.114"
        if command[0] == WINDOWS_CHROMEDRIVER:
            return "ChromeDriver 136.0.7103.114"
        return None

    monkeypatch.setenv("CHROMEDRIVER_PATH", WINDOWS_CHROMEDRIVER)
    monkeypatch.setattr(check_environment.os.path, "exists", fake_exists)
    monkeypatch.setattr(check_environment.os.path, "islink", fake_islink)
    monkeypatch.setattr(check_environment, "_run_command_get_version", fake_version)
    fake_install_dir(monkeypatch, WINDOWS_CHROME, "136.0.7103.114")

    assert check_environment.check_version_match() is True


def test_find_chrome_reads_windows_install_version_directory(monkeypatch, tmp_path):
    """
    On Windows, chrome.exe --version can open an existing browser session instead
    of printing a version, so fall back to the versioned install directory.
    """
    chrome_path = str(tmp_path / "Google" / "Chrome" / "Application" / "chrome.exe")
    version_dir = Path(chrome_path).parent / "136.0.7103.114"

    def fake_exists(path):
        return path == chrome_path

    def fake_listdir(path):
        if path == str(Path(chrome_path).parent):
            return [version_dir.name]
        return []

    def fake_isdir(path):
        return path == str(version_dir)

    monkeypatch.setattr(check_environment, "_get_chrome_paths", lambda: [chrome_path], raising=False)
    monkeypatch.setattr(check_environment.os.path, "exists", fake_exists)
    monkeypatch.setattr(check_environment.os.path, "isdir", fake_isdir)
    monkeypatch.setattr(check_environment.os, "listdir", fake_listdir)
    monkeypatch.setattr(check_environment, "_run_command_get_version", lambda command: "正在现有的浏览器会话中打开。")

    assert check_environment._find_chrome() == (
        chrome_path,
        "Google Chrome 136.0.7103.114",
        "136",
    )


def test_get_chromedriver_paths_includes_autoinstaller_cache(monkeypatch, tmp_path):
    """Environment checks should find drivers downloaded by chromedriver-autoinstaller."""
    package_dir = tmp_path / "chromedriver_autoinstaller"
    driver_dir = package_dir / "136"
    driver_dir.mkdir(parents=True)
    driver_path = driver_dir / "chromedriver.exe"
    driver_path.write_text("")

    fake_autoinstaller = SimpleNamespace(__file__=str(package_dir / "__init__.py"))
    monkeypatch.setitem(sys.modules, "chromedriver_autoinstaller", fake_autoinstaller)

    assert str(driver_path) in check_environment._get_chromedriver_paths()
