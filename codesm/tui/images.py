"""Inline figures, with terminal graphics when available and an original-file link."""

import asyncio
from urllib.parse import urlsplit

from PIL import Image, ImageOps
from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Link, Static

# Import before App.run: terminal capability probes must not compete with Textual
# for stdin while the user is typing or selecting text.
from textual_image.renderable import Image as ImageRenderable
from textual_image.widget import Image as TerminalImage, UnicodeImage

from codesm.storage.images import MAX_IMAGE_BYTES, MAX_IMAGE_PIXELS, IMAGE_FORMATS, image_path


def load_preview(path):
    if path.stat().st_size > MAX_IMAGE_BYTES:
        raise ValueError("Image exceeds the preview size limit")
    with Image.open(path, formats=list(IMAGE_FORMATS)) as image:
        if image.width * image.height > MAX_IMAGE_PIXELS:
            raise ValueError("Image exceeds the preview size limit")
        # Keep the original unchanged. Decode off the UI thread and retain only a
        # small first-frame preview, including for animated GIF/WebP files.
        preview = ImageOps.exif_transpose(image)
        preview.thumbnail((1280, 800))
        return preview.convert("RGBA")


class ImageMessage(Vertical):
    DEFAULT_CSS = """
    ImageMessage {
        height: auto;
        width: 100%;
        margin: 1 0;
        padding: 0 1;
    }
    ImageMessage .image-caption { height: auto; text-style: bold; }
    ImageMessage .image-preview {
        width: auto;
        height: auto;
        max-width: 100%;
        max-height: 22;
        pointer: pointer;
    }
    ImageMessage .image-status { height: auto; color: $text-muted; }
    ImageMessage Link { height: auto; width: auto; color: $accent; }
    """

    ALLOW_SELECT = False

    def __init__(self, caption: str, metadata: dict):
        super().__init__()
        self._preview_widget = None
        self.caption = caption
        self.metadata = metadata
        self.path = image_path(metadata)
        self.url = self.path.as_uri() if self.path and self.path.is_file() else metadata.get("source_url", "")
        try:
            if urlsplit(self.url).scheme not in {"http", "https", "file"}:
                self.url = ""
        except (TypeError, ValueError):
            self.url = ""

    def compose(self) -> ComposeResult:
        dimensions = ""
        if self.metadata.get("width") and self.metadata.get("height"):
            dimensions = f" · {self.metadata['width']} × {self.metadata['height']}"
        yield Static(Text(self.caption + dimensions), classes="image-caption")
        yield Static("Loading image…", classes="image-status", markup=False)
        if self.url:
            approximate = ImageRenderable.__module__.rsplit(".", 1)[-1] in {"halfcell", "unicode"}
            label = "Open original image · full detail" if approximate else "Open original image"
            link = Link(label, url=self.url, tooltip="Open in your default browser (Enter)")
            link.ALLOW_SELECT = False
            link.styles.pointer = "pointer"
            yield link

    async def on_mount(self):
        status = self.query_one(".image-status", Static)
        try:
            if not self.path or not self.path.is_file():
                raise ValueError(self.metadata.get("error", "Image is no longer in the local cache"))
            preview = await asyncio.to_thread(load_preview, self.path)
            image_class = TerminalImage
            if self.app.no_color and ImageRenderable.__module__.endswith(".halfcell"):
                image_class = UnicodeImage  # Color-only blocks lose the plot under NO_COLOR.
            widget = self._preview_widget = image_class(preview, classes="image-preview")
            widget.ALLOW_SELECT = False
            await self.mount(widget, before=status)
            for child in widget.walk_children():
                child.ALLOW_SELECT = False
                child.styles.pointer = "pointer"
            await status.remove()
        except (OSError, ValueError, SyntaxError, Image.DecompressionBombError):
            status.update("Preview unavailable · " + self.metadata.get("error", "open the original image"))

    def on_click(self, event: events.Click):
        if (event.button == 1 and event.widget
                and any(node.has_class("image-preview") for node in event.widget.ancestors_with_self)
                and self.url and not self.screen.get_selected_text()):
            event.stop()
            self.app.open_url(self.url)

    def on_unmount(self):
        if self._preview_widget:
            image = self._preview_widget.image
            self._preview_widget.image = None  # Also releases terminal graphics resources.
            if isinstance(image, Image.Image):
                image.close()
