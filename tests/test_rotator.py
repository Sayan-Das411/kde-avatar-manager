"""Tests for kdam.rotator — get_next_image selection logic and rotate()."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from helpers import _patch_paths  # shared: redirects all paths to tmp, silences notify


def _make_db(entries: dict):
    """Return a mock ImageDB whose active() returns the given dict."""
    db = MagicMock()
    db.active.return_value = entries
    return db


def _make_entry(weight=1.0, last_used=None):
    from kdam.db import ImageEntry
    e = ImageEntry(configured=True, enabled=True, weight=weight)
    e.last_used = last_used
    return e


def _make_config(mode="daily", order="random", sequential_index=0):
    from kdam.config import Config
    cfg = Config()
    cfg.rotation.mode = mode
    cfg.rotation.order = order
    cfg.state.current_index = sequential_index
    return cfg


# ── get_next_image ─────────────────────────────────────────────────────────────

class TestGetNextImage:
    def test_random_returns_one_of_active(self):
        from kdam.rotator import get_next_image
        entries = {"/a.jpg": _make_entry(), "/b.jpg": _make_entry(), "/c.jpg": _make_entry()}
        db = _make_db(entries)
        cfg = _make_config(order="random")
        assert get_next_image(cfg, db) in entries

    def test_random_avoids_last_when_multiple(self):
        """With multiple images, random should not pick the same as last_image_path."""
        from kdam.rotator import get_next_image
        entries = {"/a.jpg": _make_entry(), "/b.jpg": _make_entry()}
        db = _make_db(entries)
        cfg = _make_config(order="random")
        cfg.state.last_image_path = "/a.jpg"
        # Run 20 times — at least once it should pick /b.jpg
        results = {get_next_image(cfg, db) for _ in range(20)}
        assert "/b.jpg" in results

    def test_sequential_cycles(self):
        from kdam.rotator import get_next_image
        paths = ["/a.jpg", "/b.jpg", "/c.jpg"]
        entries = {p: _make_entry() for p in paths}
        db = _make_db(entries)
        cfg = _make_config(order="sequential", sequential_index=0)
        results = [get_next_image(cfg, db) for _ in range(6)]
        assert set(results[:3]) == set(paths)
        assert results[3] == results[0]   # wraps around

    def test_weighted_respects_weight(self):
        """A very high-weight image should dominate selection."""
        from kdam.rotator import get_next_image
        entries = {"/rare.jpg": _make_entry(weight=0.01), "/common.jpg": _make_entry(weight=1000.0)}
        db = _make_db(entries)
        cfg = _make_config(order="weighted")
        results = [get_next_image(cfg, db) for _ in range(50)]
        common_count = results.count("/common.jpg")
        assert common_count >= 45, f"Expected common to dominate, got {common_count}/50"

    def test_empty_active_returns_none(self):
        from kdam.rotator import get_next_image
        assert get_next_image(_make_config(), _make_db({})) is None

    def test_single_image_always_returns_it(self):
        from kdam.rotator import get_next_image
        entries = {"/solo.jpg": _make_entry()}
        db = _make_db(entries)
        cfg = _make_config(order="random")
        cfg.state.last_image_path = "/solo.jpg"
        assert get_next_image(cfg, db) == "/solo.jpg"

    def test_panic_image_excluded_from_pool(self):
        """The panic image must never be selected for normal rotation."""
        from kdam.rotator import get_next_image
        entries = {"/good.jpg": _make_entry(), "/panic.jpg": _make_entry()}
        db = _make_db(entries)
        cfg = _make_config(order="random")
        cfg.panic.path = "/panic.jpg"
        results = {get_next_image(cfg, db) for _ in range(30)}
        assert "/panic.jpg" not in results
        assert "/good.jpg" in results

    def test_lru_prioritizes_unshown_and_oldest(self):
        """LRU should restrict selection to the oldest/unshown candidates."""
        from kdam.rotator import get_next_image
        # 10 images with timestamps from 1 hour ago to 10 hours ago; 2 never shown
        entries = {
            "/unshown1.jpg": _make_entry(last_used=None),
            "/unshown2.jpg": _make_entry(last_used=None),
            "/old1.jpg": _make_entry(last_used="2026-01-01T00:00:00"),
            "/old2.jpg": _make_entry(last_used="2026-01-02T00:00:00"),
            "/recent1.jpg": _make_entry(last_used="2026-10-01T00:00:00"),
            "/recent2.jpg": _make_entry(last_used="2026-10-02T00:00:00"),
            "/recent3.jpg": _make_entry(last_used="2026-10-03T00:00:00"),
            "/recent4.jpg": _make_entry(last_used="2026-10-04T00:00:00"),
        }
        db = _make_db(entries)
        cfg = _make_config(order="lru")
        cfg.rotation.lru_pool_size = "3"  # custom K = 3 oldest

        chosen = {get_next_image(cfg, db) for _ in range(30)}
        # The 3 oldest must be /unshown1, /unshown2, and /old1
        assert chosen.issubset({"/unshown1.jpg", "/unshown2.jpg", "/old1.jpg"})
        assert "/recent1.jpg" not in chosen

    def test_lru_pool_size_formula(self):
        """Test default dynamic pool size calculations across scales."""
        from kdam.rotator import _get_lru_pool_size
        cfg = _make_config(order="lru")
        cfg.rotation.lru_pool_size = "default"

        # Small pool (N <= 10) clamped to N
        assert _get_lru_pool_size(5, cfg) == 5
        # Medium pool (10 < N <= 50) uses minimum 10
        assert _get_lru_pool_size(30, cfg) == 10
        assert _get_lru_pool_size(50, cfg) == 10
        # Larger pool uses 15%
        assert _get_lru_pool_size(100, cfg) == 15
        assert _get_lru_pool_size(200, cfg) == 30
        # Huge pool capped at 50
        assert _get_lru_pool_size(600, cfg) == 50
        assert _get_lru_pool_size(2000, cfg) == 50

    def test_lru_custom_pool_size_clamps_to_n(self):
        """Custom K is accepted even if K > N, clamped to N at selection time."""
        from kdam.rotator import _get_lru_pool_size
        cfg = _make_config(order="lru")
        cfg.rotation.lru_pool_size = "25"

        assert _get_lru_pool_size(5, cfg) == 5
        assert _get_lru_pool_size(30, cfg) == 25

    def test_deck_mode_cycles_and_handles_new_images(self):
        """Deck mode should show every image once before repeating, and include new images."""
        from kdam.rotator import get_next_image
        paths = ["/a.jpg", "/b.jpg", "/c.jpg"]
        entries = {p: _make_entry() for p in paths}
        db = _make_db(entries)
        cfg = _make_config(order="deck")

        # First cycle of 3
        cycle1 = [get_next_image(cfg, db) for _ in range(3)]
        assert set(cycle1) == set(paths)

        # Mid-cycle, a new image /d.jpg is added
        paths.append("/d.jpg")
        entries["/d.jpg"] = _make_entry()
        db = _make_db(entries)

        # In cycle 2, all 4 images must be picked once
        cycle2 = [get_next_image(cfg, db) for _ in range(4)]
        assert set(cycle2) == set(paths)


# ── rotate() ──────────────────────────────────────────────────────────────────

class TestRotateFunction:
    def test_rotate_calls_kde_set_avatar(self, tmp_path, monkeypatch):
        """rotate() must ultimately call kde.set_avatar with the processed image.

        ALL real system paths are redirected to tmp_path — this test must NEVER
        write to ~/.kde-avatar-manager/config.json or rotation.log.
        """
        _patch_paths(tmp_path, monkeypatch)

        from kdam import rotator
        from kdam.db import ImageEntry, CropInfo

        img_path = str(tmp_path / "img.jpg")
        entry = ImageEntry(
            configured=True, enabled=True,
            crop=CropInfo(x=0, y=0, size=10),
        )
        (tmp_path / "img.jpg").write_bytes(b"FAKEJPEG")   # must exist on disk

        db = _make_db({img_path: entry})
        db.get.return_value = entry
        db.set = MagicMock()
        db.save = MagicMock()
        db.stale.return_value = {}

        cfg = _make_config(order="random")

        # Must patch save_config — rotate() persists state to config.json
        monkeypatch.setattr(rotator, "save_config", MagicMock())
        monkeypatch.setattr(rotator, "get_next_image", lambda c, d: img_path)
        # process_image(source_path, crop) → Path  (2-arg; no out_dir)
        monkeypatch.setattr(rotator, "process_image", lambda path, crop: tmp_path / "out.png")

        set_calls = []
        monkeypatch.setattr(rotator.kde, "set_avatar", lambda p: set_calls.append(str(p)))

        result = rotator.rotate(cfg, db)
        assert result is True
        assert len(set_calls) == 1

    def test_rotate_returns_none_when_no_images(self, tmp_path, monkeypatch):
        """Empty pool returns None (not False) so callers can distinguish pool-empty from failure."""
        _patch_paths(tmp_path, monkeypatch)
        from kdam import rotator
        monkeypatch.setattr(rotator, "get_next_image", lambda c, d: None)
        result = rotator.rotate(_make_config(), _make_db({}))
        assert result is None

    def test_rotate_returns_false_when_image_missing_on_disk(self, tmp_path, monkeypatch):
        """If the selected image file has disappeared, rotate() should fail gracefully."""
        _patch_paths(tmp_path, monkeypatch)
        from kdam import rotator
        ghost = str(tmp_path / "ghost.jpg")   # does NOT exist
        monkeypatch.setattr(rotator, "get_next_image", lambda c, d: ghost)
        result = rotator.rotate(_make_config(), _make_db({ghost: _make_entry()}))
        assert result is False

    def test_rotate_logs_to_tmp_not_real_log(self, tmp_path, monkeypatch):
        """Verify the log write goes to the isolated tmp log, not the real one."""
        _patch_paths(tmp_path, monkeypatch)

        from kdam import rotator
        from kdam.db import ImageEntry, CropInfo
        from kdam.config import LOG_FILE as REAL_LOG  # check before patch takes effect
        import kdam.config as cfg_mod

        img_path = str(tmp_path / "img.jpg")
        entry = ImageEntry(configured=True, enabled=True,
                           crop=CropInfo(x=0, y=0, size=10))
        (tmp_path / "img.jpg").write_bytes(b"FAKE")

        db = _make_db({img_path: entry})
        db.get.return_value = entry
        db.set = MagicMock()
        db.save = MagicMock()
        db.stale.return_value = {}

        monkeypatch.setattr(rotator, "save_config", MagicMock())
        monkeypatch.setattr(rotator, "get_next_image", lambda c, d: img_path)
        monkeypatch.setattr(rotator, "process_image", lambda p, c: tmp_path / "out.png")
        monkeypatch.setattr(rotator.kde, "set_avatar", MagicMock())

        rotator.rotate(_make_config(), db)

        isolated_log = tmp_path / "logs" / "rotation.log"
        assert isolated_log.exists(), "Log should have been written to the isolated tmp dir"
        # The real log must NOT have been modified
        real_log = Path.home() / ".kde-avatar-manager" / "logs" / "rotation.log"
        if real_log.exists():
            assert str(tmp_path) not in real_log.read_text(), \
                "Test wrote to the REAL rotation.log — isolation is broken!"

    def test_process_image_creates_temp_file(self, tmp_path, monkeypatch):
        """process_image should create a 256x256 PNG file in PROCESSED_DIR."""
        _patch_paths(tmp_path, monkeypatch)
        from PIL import Image
        from kdam import rotator
        from kdam.db import CropInfo

        src = tmp_path / "src.png"
        Image.new("RGBA", (100, 100), (255, 0, 0, 255)).save(str(src), "PNG")

        out = rotator.process_image(src, CropInfo(x=10, y=10, size=50))
        try:
            assert out.exists()
            assert out.parent == tmp_path / "processed"
            im = Image.open(str(out))
            assert im.size == (256, 256)
        finally:
            out.unlink(missing_ok=True)

    def test_rotate_cleans_up_temp_file(self, tmp_path, monkeypatch):
        """rotate() must delete the temporary processed file after applying."""
        _patch_paths(tmp_path, monkeypatch)
        from PIL import Image
        from kdam import rotator
        from kdam.db import ImageEntry, CropInfo

        src = tmp_path / "src.png"
        Image.new("RGBA", (100, 100), (0, 255, 0, 255)).save(str(src), "PNG")

        entry = ImageEntry(configured=True, enabled=True, crop=CropInfo(x=0, y=0, size=100))
        db = _make_db({str(src): entry})
        db.get.return_value = entry
        db.set = MagicMock()
        db.save = MagicMock()
        db.stale.return_value = {}

        created_files = []
        real_process = rotator.process_image
        def tracking_process(s, c):
            p = real_process(s, c)
            created_files.append(p)
            return p

        monkeypatch.setattr(rotator, "process_image", tracking_process)
        monkeypatch.setattr(rotator, "save_config", MagicMock())
        monkeypatch.setattr(rotator, "get_next_image", lambda c, d: str(src))
        monkeypatch.setattr(rotator.kde, "set_avatar", MagicMock())

        res = rotator.rotate(_make_config(), db)
        assert res is True
        assert len(created_files) == 1
        assert not created_files[0].exists(), "Temporary processed file was not cleaned up!"
