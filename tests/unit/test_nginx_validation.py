from types import SimpleNamespace

from nginx.Nginx import Nginx


def test_validate_config_restores_previous_config_after_failure(tmp_path, monkeypatch):
    config_file = tmp_path / "conf.d" / "nginx-proxy.conf"
    config_file.parent.mkdir()
    config_file.write_text("previous config")

    nginx = Nginx(str(config_file), str(tmp_path / "challenges"))

    def fake_run(command, stdout=None, stderr=None):
        assert config_file.read_text() == "candidate config"
        return SimpleNamespace(returncode=1, stderr=b'nginx: [emerg] invalid in nginx-proxy.conf:1\n')

    monkeypatch.setattr("nginx.Nginx.subprocess.run", fake_run)

    valid, error = nginx.validate_config("candidate config")

    assert valid is False
    assert "invalid" in error
    assert config_file.read_text() == "previous config"
    assert nginx.last_working_config == "previous config"


def test_validate_config_restores_previous_config_after_success(tmp_path, monkeypatch):
    config_file = tmp_path / "conf.d" / "nginx-proxy.conf"
    config_file.parent.mkdir()
    config_file.write_text("previous config")

    nginx = Nginx(str(config_file), str(tmp_path / "challenges"))

    def fake_run(command, stdout=None, stderr=None):
        assert config_file.read_text() == "candidate config"
        return SimpleNamespace(returncode=0, stderr=b"")

    monkeypatch.setattr("nginx.Nginx.subprocess.run", fake_run)

    valid, error = nginx.validate_config("candidate config")

    assert valid is True
    assert error is None
    assert config_file.read_text() == "previous config"
    assert nginx.last_working_config == "previous config"


def test_update_config_rejects_invalid_candidate_before_reload(tmp_path, monkeypatch):
    config_file = tmp_path / "conf.d" / "nginx-proxy.conf"
    config_file.parent.mkdir()
    config_file.write_text("previous config")

    nginx = Nginx(str(config_file), str(tmp_path / "challenges"))
    commands = []

    def fake_run(command, stdout=None, stderr=None):
        commands.append(command)
        assert command == Nginx.command_config_test
        return SimpleNamespace(returncode=1, stderr=b'nginx: [emerg] invalid in nginx-proxy.conf:1\n')

    monkeypatch.setattr("nginx.Nginx.subprocess.run", fake_run)

    assert nginx.update_config("candidate config") is False
    assert config_file.read_text() == "previous config"
    assert commands == [Nginx.command_config_test]


def test_update_config_can_skip_validation_for_prevalidated_candidate(tmp_path, monkeypatch):
    config_file = tmp_path / "conf.d" / "nginx-proxy.conf"
    config_file.parent.mkdir()
    config_file.write_text("previous config")

    nginx = Nginx(str(config_file), str(tmp_path / "challenges"))
    commands = []

    def fake_run(command, stdout=None, stderr=None):
        commands.append(command)
        assert command == Nginx.command_reload
        return SimpleNamespace(returncode=0, stderr=b"")

    monkeypatch.setattr("nginx.Nginx.subprocess.run", fake_run)

    assert nginx.update_config("candidate config", validate=False) is True
    assert config_file.read_text() == "candidate config"
    assert nginx.last_working_config == "candidate config"
    assert commands == [Nginx.command_reload]
