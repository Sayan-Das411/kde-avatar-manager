"""Tests for kdam.config — load_config, save_config, dataclasses."""

from __future__ import annotations

from pathlib import Path


def _patch_paths(tmp: Path, monkeypatch):
    import kdam.config as cfg

    monkeypatch.setattr(cfg, "CONFIG_DIR", tmp)
    monkeypatch.setattr(cfg, "CONFIG_FILE", tmp / "config.json")
    monkeypatch.setattr(cfg, "IMAGES_FILE", tmp / "images.json")
    monkeypatch.setattr(cfg, "PROCESSED_DIR", tmp / "processed")
    monkeypatch.setattr(cfg, "BACKUP_FILE", tmp / "backup.png")
    monkeypatch.setattr(cfg, "LOGS_DIR", tmp / "logs")
    monkeypatch.setattr(cfg, "LOG_FILE", tmp / "logs" / "rotation.log")
    monkeypatch.setattr(cfg, "_LOCK_FILE", tmp / ".lock")
    cfg.ensure_dirs()


class TestLoadSaveConfig:
    def test_defaults_when_missing(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        from kdam.config import load_config

        cfg = load_config()
        assert cfg.rotation.mode == "daily"
        assert cfg.directories == []
        assert cfg.panic.path is None
        assert cfg.state.enabled is True  # rotation is ON by default

    def test_round_trip(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        from kdam.config import Directory, load_config, save_config

        cfg = load_config()
        cfg.rotation.mode = "interval"
        cfg.rotation.interval_hours = 4
        cfg.state.enabled = True
        cfg.directories.append(Directory(path="/tmp/avatars", recursive=True))
        save_config(cfg)

        cfg2 = load_config()
        assert cfg2.rotation.mode == "interval"
        assert cfg2.rotation.interval_hours == 4
        assert cfg2.state.enabled is True
        assert len(cfg2.directories) == 1
        assert cfg2.directories[0].path == "/tmp/avatars"
        assert cfg2.directories[0].recursive is True

    def test_corrupt_gives_defaults(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        (tmp_path / "config.json").write_text("}{bad json")
        from kdam.config import load_config

        cfg = load_config()
        assert cfg.rotation.mode == "daily"

    def test_atomic_write(self, tmp_path, monkeypatch):
        """save_config must not leave a .tmp file behind."""
        _patch_paths(tmp_path, monkeypatch)
        from kdam.config import load_config, save_config

        cfg = load_config()
        save_config(cfg)
        tmp_files = list(tmp_path.glob("*.tmp"))
        assert tmp_files == [], f"Leftover tmp files: {tmp_files}"

    def test_panic_config_round_trip(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        from kdam.config import load_config, save_config

        cfg = load_config()
        cfg.panic.path = "/home/user/safe.jpg"
        cfg.panic.crop = {"x": 10, "y": 20, "size": 100}
        save_config(cfg)

        cfg2 = load_config()
        assert cfg2.panic.path == "/home/user/safe.jpg"
        assert cfg2.panic.crop == {"x": 10, "y": 20, "size": 100}

    def test_lru_and_deck_config_round_trip(self, tmp_path, monkeypatch):
        _patch_paths(tmp_path, monkeypatch)
        from kdam.config import load_config, save_config

        cfg = load_config()
        cfg.rotation.order = "lru"
        cfg.rotation.lru_pool_size = "15"
        cfg.state.deck = ["/a.jpg", "/b.jpg"]
        save_config(cfg)

        cfg2 = load_config()
        assert cfg2.rotation.order == "lru"
        assert cfg2.rotation.lru_pool_size == "15"
        assert cfg2.state.deck == ["/a.jpg", "/b.jpg"]


class TestDirectory:
    def test_round_trip(self):
        from kdam.config import Directory

        d = Directory(path="/foo", recursive=True, enabled=False)
        d2 = Directory.from_dict(d.to_dict())
        assert d2.path == "/foo"
        assert d2.recursive is True
        assert d2.enabled is False

    def test_defaults(self):
        from kdam.config import Directory

        d = Directory.from_dict({"path": "/bar"})
        assert d.recursive is False
        assert d.enabled is True
