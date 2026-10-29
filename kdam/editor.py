"""PyQt5 crop editor — lets the user drag-select the square avatar crop region.

Performance design
------------------
- The source PIL image is pre-scaled once at load time to a display-fit QPixmap
  (stored in self._display_pm).  paintEvent never touches PIL during drag.
- A separate "fast source" (max 800 px, BILINEAR) is pre-built for preview
  cropping so the preview never has to crop a 3000-px original.
- Preview updates are throttled to ~30 fps via a QTimer so rapid mouse moves
  don't pile up work.
- QFont::fromString warnings from KDE's font config are silently suppressed via
  a custom Qt message handler installed once per process.

Controls
--------
- Drag inside crop square  → move
- Drag bottom-right handle → resize
- Scroll wheel             → resize (±5%)
- Arrow keys               → nudge 1 px  (Shift → 10 px)
- +/–                      → resize
- Reset button             → re-centre crop square to image centre

Return values of open_crop_editor()
------------------------------------
- dict {x, y, size}  — user saved a crop
- None               — user skipped (closed window or clicked Skip)
- EDITOR_QUIT        — user clicked Quit (stop configure-all)
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image
from PyQt5.QtCore import QPoint, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import (
    QColor,
    QImage,
    QKeyEvent,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PyQt5.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

PREVIEW_SIZE = 170    # px — circular preview diameter
HANDLE_SIZE  = 14     # px — resize handle corner square
MIN_CROP_PX  = 32     # minimum crop size in image pixels

class EditorQuit:
    """Typed singleton sentinel returned by open_crop_editor when user clicks Quit.

    Using a class instead of ``object()`` lets callers annotate against
    ``EditorQuit`` and lets mypy verify ``result is EDITOR_QUIT`` checks.
    """
    _instance: EditorQuit | None = None

    def __new__(cls) -> EditorQuit:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __bool__(self) -> bool:
        return False

    def __repr__(self) -> str:
        return "EDITOR_QUIT"


# Module-level singleton — callers use `result is EDITOR_QUIT`
EDITOR_QUIT: EditorQuit = EditorQuit()

# Max side length for the pre-scaled display pixmap
_MAX_DISPLAY_PX = 1800
# Max side length for the fast-preview source image (PIL, for cropping)
_MAX_PREVIEW_SRC_PX = 800


# ── Silence KDE font-config Qt warnings ───────────────────────────────────────

def _qt_msg_handler(msg_type: int, context, message: str) -> None:
    """Drop harmless 'QFont::fromString: Invalid description' noise from KDE."""
    if "QFont::fromString" in message:
        return
    # Everything else: write to stderr as Qt normally would
    import sys as _sys
    _sys.stderr.write(message + "\n")


# ── PIL ↔ QImage helper ───────────────────────────────────────────────────────

def _pil_to_qimage(pil_img: Image.Image) -> QImage:
    pil_img = pil_img.convert("RGBA")
    data = pil_img.tobytes("raw", "RGBA")
    qimg = QImage(data, pil_img.width, pil_img.height,
                  4 * pil_img.width, QImage.Format_RGBA8888)
    qimg._buffer = data  # prevent GC — QImage does NOT copy the buffer
    return qimg


def _pil_to_pixmap(pil_img: Image.Image) -> QPixmap:
    return QPixmap.fromImage(_pil_to_qimage(pil_img))


# ── Circular preview widget ───────────────────────────────────────────────────

class CirclePreview(QWidget):
    """Live circular preview — throttled to ≤30 fps so it never blocks drag."""

    def __init__(self, fast_src: Image.Image, fast_scale: float,
                 size: int = PREVIEW_SIZE, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._fast_src   = fast_src    # pre-scaled PIL image for cheap cropping
        self._fast_scale = fast_scale  # ratio: fast_src_px / original_px
        self._pixmap: QPixmap | None = None
        self.setFixedSize(size, size)

        # Throttle: at most one PIL operation per 33 ms (~30 fps)
        self._pending: dict | None = None   # crop dict in original coords
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self._flush)

    def update_crop(self, crop: dict) -> None:
        """Queue a preview redraw for the given crop (original image coords)."""
        self._pending = crop
        if not self._timer.isActive():
            self._flush()   # render immediately for the first event in a burst

    def _flush(self) -> None:
        if self._pending is None:
            return
        crop = self._pending
        self._pending = None

        # Map original crop coords → fast-source coords
        x = int(crop["x"] * self._fast_scale)
        y = int(crop["y"] * self._fast_scale)
        s = max(1, int(crop["size"] * self._fast_scale))
        iw, ih = self._fast_src.size
        x = max(0, min(x, iw - 1))
        y = max(0, min(y, ih - 1))
        s = max(1, min(s, iw - x, ih - y))

        side = self.width()
        cropped = (self._fast_src
                   .crop((x, y, x + s, y + s))
                   .resize((side, side), Image.BILINEAR))
        self._pixmap = _pil_to_pixmap(cropped)
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        s = self.width()

        if self._pixmap:
            clip = QPainterPath()
            clip.addEllipse(0, 0, s, s)
            p.setClipPath(clip)
            p.drawPixmap(0, 0, self._pixmap)
            p.setClipping(False)
        else:
            p.fillRect(0, 0, s, s, QColor(60, 60, 60))

        p.setPen(QPen(QColor(180, 180, 180), 2))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(1, 1, s - 2, s - 2)


# ── Interactive crop canvas ───────────────────────────────────────────────────

class ImageCropWidget(QWidget):
    """Image canvas with a draggable + resizable crop square.

    paintEvent only ever draws the pre-cached QPixmap — no PIL work during drag.
    """

    crop_changed = pyqtSignal(int, int, int)   # x, y, size — original image coords

    def __init__(self,
                 pil_image: Image.Image,
                 display_pm: QPixmap,           # pre-built, display-fit pixmap
                 initial_crop: dict | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.iw, self.ih = pil_image.size
        self._display_pm = display_pm           # never recomputed during drag

        # Initialise crop square
        if initial_crop and all(k in initial_crop for k in ("x", "y", "size")):
            self.crop_x    = int(initial_crop["x"])
            self.crop_y    = int(initial_crop["y"])
            self.crop_size = int(initial_crop["size"])
        else:
            self.crop_size = min(self.iw, self.ih)
            self.crop_x    = (self.iw - self.crop_size) // 2
            self.crop_y    = (self.ih - self.crop_size) // 2
        self._clamp()

        # Drag state
        self._drag_mode: str | None = None   # 'move' | 'resize'
        self._drag_origin: QPoint = QPoint()
        self._drag_start: tuple[int, int, int] = (self.crop_x, self.crop_y, self.crop_size)

        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMinimumSize(400, 300)

    # ── Coordinate helpers ────────────────────────────────────────────────────

    def _display_params(self) -> tuple[int, int, float, int, int]:
        """(disp_w, disp_h, scale, offset_x, offset_y) to fit image in widget."""
        ww, wh = self.width(), self.height()
        scale = min(ww / self.iw, wh / self.ih)
        dw, dh = int(self.iw * scale), int(self.ih * scale)
        return dw, dh, scale, (ww - dw) // 2, (wh - dh) // 2

    def _img_to_w(self, ix: int, iy: int) -> tuple[int, int]:
        _, _, scale, ox, oy = self._display_params()
        return int(ix * scale) + ox, int(iy * scale) + oy

    def _clamp(self) -> None:
        self.crop_size = max(MIN_CROP_PX, min(self.crop_size, self.iw, self.ih))
        self.crop_x    = max(0, min(self.crop_x, self.iw - self.crop_size))
        self.crop_y    = max(0, min(self.crop_y, self.ih - self.crop_size))

    def get_crop(self) -> dict:
        return {"x": self.crop_x, "y": self.crop_y, "size": self.crop_size}

    # ── Paint — PIL-free ──────────────────────────────────────────────────────

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform)

        dw, dh, scale, ox, oy = self._display_params()

        # Draw pre-cached display pixmap (scaled to fit, fast)
        scaled = self._display_pm.scaled(dw, dh, Qt.KeepAspectRatio,
                                          Qt.SmoothTransformation)
        p.drawPixmap(ox, oy, scaled)

        # Crop square in widget coords
        cx, cy = self._img_to_w(self.crop_x, self.crop_y)
        cs = max(1, int(self.crop_size * scale))

        # Dark overlay — four rects surrounding the crop area
        dim = QColor(0, 0, 0, 140)
        p.fillRect(ox,      oy,       dw,                cy - oy,          dim)
        p.fillRect(ox,      cy + cs,  dw,                oy + dh - cy - cs, dim)
        p.fillRect(ox,      cy,       cx - ox,           cs,               dim)
        p.fillRect(cx + cs, cy,       ox + dw - cx - cs, cs,               dim)

        # Crop border
        p.setPen(QPen(Qt.white, 2))
        p.setBrush(Qt.NoBrush)
        p.drawRect(cx, cy, cs, cs)

        # Rule-of-thirds
        third = cs // 3
        p.setPen(QPen(QColor(255, 255, 255, 65), 1))
        for i in (1, 2):
            p.drawLine(cx + third * i, cy, cx + third * i, cy + cs)
            p.drawLine(cx, cy + third * i, cx + cs, cy + third * i)

        # Resize handle (bottom-right white square)
        p.fillRect(cx + cs - HANDLE_SIZE, cy + cs - HANDLE_SIZE,
                   HANDLE_SIZE, HANDLE_SIZE, QColor(255, 255, 255, 210))

    # ── Hit tests ─────────────────────────────────────────────────────────────

    def _in_resize_handle(self, wx: int, wy: int) -> bool:
        cx, cy = self._img_to_w(self.crop_x, self.crop_y)
        _, _, scale, _, _ = self._display_params()
        cs = int(self.crop_size * scale)
        return (cx + cs - HANDLE_SIZE) <= wx <= (cx + cs) and \
               (cy + cs - HANDLE_SIZE) <= wy <= (cy + cs)

    def _in_crop(self, wx: int, wy: int) -> bool:
        cx, cy = self._img_to_w(self.crop_x, self.crop_y)
        _, _, scale, _, _ = self._display_params()
        cs = int(self.crop_size * scale)
        return cx <= wx <= cx + cs and cy <= wy <= cy + cs

    # ── Mouse events ──────────────────────────────────────────────────────────

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            pos = event.pos()
            if self._in_resize_handle(pos.x(), pos.y()):
                self._drag_mode = "resize"
            elif self._in_crop(pos.x(), pos.y()):
                self._drag_mode = "move"
            else:
                self._drag_mode = None
                return
            self._drag_origin = pos
            self._drag_start  = (self.crop_x, self.crop_y, self.crop_size)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        pos = event.pos()

        # Cursor shape
        if self._in_resize_handle(pos.x(), pos.y()):
            self.setCursor(Qt.SizeFDiagCursor)
        elif self._in_crop(pos.x(), pos.y()):
            self.setCursor(Qt.SizeAllCursor)
        else:
            self.setCursor(Qt.ArrowCursor)

        if self._drag_mode is None:
            return

        _, _, scale, _, _ = self._display_params()
        dx = (pos.x() - self._drag_origin.x()) / scale
        dy = (pos.y() - self._drag_origin.y()) / scale
        sx, sy, ss = self._drag_start

        if self._drag_mode == "move":
            self.crop_x = int(sx + dx)
            self.crop_y = int(sy + dy)
        elif self._drag_mode == "resize":
            delta = dx if abs(dx) >= abs(dy) else dy
            self.crop_size = int(ss + delta)

        self._clamp()
        self.update()                                              # repaint (no PIL)
        self.crop_changed.emit(self.crop_x, self.crop_y, self.crop_size)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self._drag_mode = None

    def wheelEvent(self, event) -> None:  # noqa: N802
        step = max(4, self.crop_size // 20)
        self.crop_size += step if event.angleDelta().y() > 0 else -step
        self._clamp()
        self.update()
        self.crop_changed.emit(self.crop_x, self.crop_y, self.crop_size)

    def keyPressEvent(self, event: QKeyEvent | None) -> None:  # noqa: N802
        if event is None:
            return
        step = 10 if (event.modifiers() & Qt.ShiftModifier) else 1
        k = event.key()
        if k == Qt.Key_Left:
            self.crop_x = max(0, self.crop_x - step)
        elif k == Qt.Key_Right:
            self.crop_x = min(self.iw - self.crop_size, self.crop_x + step)
        elif k == Qt.Key_Up:
            self.crop_y = max(0, self.crop_y - step)
        elif k == Qt.Key_Down:
            self.crop_y = min(self.ih - self.crop_size, self.crop_y + step)
        elif k in (Qt.Key_Plus, Qt.Key_Equal):
            self.crop_size = min(min(self.iw, self.ih), self.crop_size + step * 4)
        elif k == Qt.Key_Minus:
            self.crop_size = max(MIN_CROP_PX, self.crop_size - step * 4)
        else:
            event.ignore()
            super().keyPressEvent(event)
            return
        self._clamp()
        self.update()
        self.crop_changed.emit(self.crop_x, self.crop_y, self.crop_size)


# ── Main window ───────────────────────────────────────────────────────────────

class CropEditorWindow(QMainWindow):
    """Main crop editor window.

    result_crop is set on Save.
    _quit is set True when user clicks Quit (vs. Skip/close which leaves it False).
    """

    def __init__(self, image_path: str,
                 initial_crop: dict | None = None) -> None:
        super().__init__()
        self.result_crop: dict | None = None
        self._quit: bool = False
        self.image_path = image_path

        try:
            pil_image = Image.open(image_path)
            # Warn but allow small images (they'll be upscaled on save)
            if pil_image.width < 64 or pil_image.height < 64:
                QMessageBox.warning(
                    self, "Small Image",
                    f"Image is {pil_image.width}×{pil_image.height} px.\n"
                    "It will be upscaled — quality may be reduced.",
                )
            self.pil_image = pil_image
        except Exception as exc:
            QMessageBox.critical(self, "Cannot open image", str(exc))
            self.close()
            return

        # ── Pre-compute display pixmap (once; never rebuilt during drag) ──────
        iw, ih = pil_image.size
        if max(iw, ih) > _MAX_DISPLAY_PX:
            r = _MAX_DISPLAY_PX / max(iw, ih)
            dp_w, dp_h = int(iw * r), int(ih * r)
            display_pil = pil_image.convert("RGBA").resize((dp_w, dp_h), Image.LANCZOS)
        else:
            display_pil = pil_image.convert("RGBA")
        self._display_pm = _pil_to_pixmap(display_pil)

        # ── Pre-compute fast preview source (small PIL for cheap cropping) ────
        if max(iw, ih) > _MAX_PREVIEW_SRC_PX:
            r = _MAX_PREVIEW_SRC_PX / max(iw, ih)
            pw, ph = int(iw * r), int(ih * r)
            self._fast_src   = pil_image.convert("RGBA").resize((pw, ph), Image.BILINEAR)
            self._fast_scale = r
        else:
            self._fast_src   = pil_image.convert("RGBA")
            self._fast_scale = 1.0

        self.setWindowTitle(f"Crop Editor — {Path(image_path).name}")
        self.setMinimumSize(720, 480)
        self._build_ui(initial_crop)

    def _build_ui(self, initial_crop: dict | None) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        layout = QHBoxLayout(root)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(10)

        # ── Left: crop canvas ─────────────────────────────────────────────────
        self.crop_widget = ImageCropWidget(
            self.pil_image, self._display_pm, initial_crop)
        self.crop_widget.crop_changed.connect(self._on_crop_changed)
        layout.addWidget(self.crop_widget, stretch=1)

        # ── Right: sidebar ────────────────────────────────────────────────────
        sidebar = QWidget()
        sidebar.setFixedWidth(200)
        sb = QVBoxLayout(sidebar)
        sb.setContentsMargins(4, 4, 4, 4)
        sb.setSpacing(10)

        lbl = QLabel("Preview")
        lbl.setAlignment(Qt.AlignCenter)
        sb.addWidget(lbl)

        self.preview = CirclePreview(self._fast_src, self._fast_scale, PREVIEW_SIZE)
        pw = QWidget()
        pl = QHBoxLayout(pw)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.addStretch()
        pl.addWidget(self.preview)
        pl.addStretch()
        sb.addWidget(pw)

        self.coord_label = QLabel()
        self.coord_label.setAlignment(Qt.AlignCenter)
        self.coord_label.setStyleSheet("color: #888; font-size: 11px;")
        sb.addWidget(self.coord_label)

        help_lbl = QLabel(
            "<b>Controls</b><br>"
            "Drag inside → move<br>"
            "Drag ◼ corner → resize<br>"
            "Scroll wheel → resize<br>"
            "← ↑ → ↓ → nudge 1 px<br>"
            "Shift + arrows → 10 px<br>"
            "+ / − → resize"
        )
        help_lbl.setStyleSheet("color: #777; font-size: 11px;")
        help_lbl.setWordWrap(True)
        sb.addWidget(help_lbl)

        sb.addStretch()

        # ── Buttons ───────────────────────────────────────────────────────────
        save_btn = QPushButton("✓  Save Crop")
        save_btn.setDefault(True)
        save_btn.setToolTip("Save this crop and continue")
        save_btn.clicked.connect(self._save)
        sb.addWidget(save_btn)

        reset_btn = QPushButton("↺  Reset Frame")
        reset_btn.setToolTip("Re-centre the crop square to the middle of the image")
        reset_btn.clicked.connect(self._reset_crop)
        sb.addWidget(reset_btn)

        skip_btn = QPushButton("→  Skip")
        skip_btn.setToolTip("Skip this image and go to the next one")
        skip_btn.clicked.connect(self._skip)
        sb.addWidget(skip_btn)

        quit_btn = QPushButton("✕  Quit")
        quit_btn.setToolTip("Stop configure-all and exit the editor")
        quit_btn.setStyleSheet("color: #c66;")
        quit_btn.clicked.connect(self._quit_editor)
        sb.addWidget(quit_btn)

        layout.addWidget(sidebar)

        # Trigger first preview
        self._on_crop_changed(
            self.crop_widget.crop_x,
            self.crop_widget.crop_y,
            self.crop_widget.crop_size,
        )

    def _on_crop_changed(self, x: int, y: int, size: int) -> None:
        self.preview.update_crop({"x": x, "y": y, "size": size})
        self.coord_label.setText(f"x={x}  y={y}\n{size} × {size} px")

    def _save(self) -> None:
        self.result_crop = self.crop_widget.get_crop()
        self.close()

    def _reset_crop(self) -> None:
        """Re-centre the crop square to the middle of the image."""
        w = self.crop_widget
        w.crop_size = min(w.iw, w.ih)
        w.crop_x    = (w.iw - w.crop_size) // 2
        w.crop_y    = (w.ih - w.crop_size) // 2
        w._clamp()
        w.update()
        w.crop_changed.emit(w.crop_x, w.crop_y, w.crop_size)

    def _skip(self) -> None:
        """Skip this image — result_crop stays None, _quit stays False."""
        self.close()

    def _quit_editor(self) -> None:
        """Signal the caller to stop configure-all."""
        self._quit = True
        self.close()

    def keyPressEvent(self, event: QKeyEvent | None) -> None:  # noqa: N802
        if event is None:
            return
        k = event.key()
        if k in (Qt.Key_Return, Qt.Key_Enter):
            self._save()
        elif k == Qt.Key_Escape:
            self._skip()
        else:
            super().keyPressEvent(event)


# ── Public API ────────────────────────────────────────────────────────────────

_msg_handler_installed = False


def open_crop_editor(image_path: str,
                     initial_crop: dict | None = None) -> dict[str, int] | EditorQuit | None:
    """Open the crop editor and block until the user saves, skips, or quits.

    Returns:
        dict {x, y, size}  — user saved a crop (use as CropInfo kwargs)
        None               — user skipped (closed window or clicked Skip)
        EDITOR_QUIT        — user clicked Quit (caller should stop configure-all)

    Callers must check ``result is EDITOR_QUIT`` before treating None as skip.
    Safe to call multiple times in the same process.
    """  # type: ignore[return]  # EDITOR_QUIT is an object(), not Optional[Dict]; typed loosely for callers
    global _msg_handler_installed

    # MUST be installed before QApplication() — the font warnings fire during
    # app construction, so installing after is too late.
    if not _msg_handler_installed:
        from PyQt5.QtCore import qInstallMessageHandler
        qInstallMessageHandler(_qt_msg_handler)  # type: ignore[arg-type]
        _msg_handler_installed = True

    app = QApplication.instance() or QApplication(sys.argv)

    window = CropEditorWindow(image_path, initial_crop)
    # Connect window close → app.quit so exec_() exits cleanly every call.
    # Qt.UniqueConnection prevents duplicate slots accumulating across repeated
    # open_crop_editor() calls (e.g. configure-all over 50 images).
    from PyQt5.QtCore import Qt
    window.destroyed.connect(app.quit, Qt.UniqueConnection)  # type: ignore[attr-defined,call-arg]
    window.show()
    app.exec_()

    if window._quit:
        return EDITOR_QUIT
    return window.result_crop
