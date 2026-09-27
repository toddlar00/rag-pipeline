"""Fixed, optional natural-pixel presentation for original crop previews.

The selectors intentionally target the installed Gradio ImagePreview structure;
they require browser rechecking on Gradio upgrades. This is presentation only:
no source-document bytes, image URL, reference, session token or callback is accessed by
the script. A detail image pixel occupies one CSS pixel, not one device pixel.
Fit uses the unmodified Image layout. Image postprocessing/cache policy remains
owned by Gradio and the authenticated launcher's private temporary directory.
"""

from __future__ import annotations


LIVE_PREVIEW_ID = "ocr-live-source-preview"
ARCHIVE_PREVIEW_ID = "ocr-archive-source-preview"
PREVIEW_CHOICES = [("Fit", "fit"), ("288 DPI", "dpi288"), ("576 DPI", "dpi576")]
DETAIL_CLASS = "ocr-preview-detail"

_ROOT_SELECTOR = f":is(#{LIVE_PREVIEW_ID}, #{ARCHIVE_PREVIEW_ID}).{DETAIL_CLASS}"
PREVIEW_CSS = "\n".join((
    f"{_ROOT_SELECTOR} {{ min-width: 0; border-radius: 0; }}",
    f"{_ROOT_SELECTOR} .image-container {{ height: auto; min-width: 0; max-width: 100%; }}",
    f"{_ROOT_SELECTOR} .image-container > button {{\n"
    "  display: block; box-sizing: border-box; width: 100%; height: auto;\n"
    "  max-height: min(60vh, 640px); overflow: auto; overscroll-behavior: contain; padding: 0;\n"
    "  border-radius: 0; outline: none; box-shadow: none;\n}",
    f"{_ROOT_SELECTOR} .image-container > button > .image-frame {{\n"
    "  display: block; width: max-content; height: auto;\n}",
    f"{_ROOT_SELECTOR} .image-container > button > .image-frame > img {{\n"
    "  display: block; width: auto; height: auto; max-width: none; max-height: none; object-fit: none;\n}",
    f"{_ROOT_SELECTOR}:focus-within {{\n"
    "  outline: 2px solid var(--color-accent, #2563eb); outline-offset: 2px;\n}",
))

# Capture precedes the installed delegated ImagePreview click -> select handler.
# buttons=[] removes toolbar actions, but does NOT remove that latent selection.
# This is deliberately not a general hotkey service and does not replace Svelte
# handlers. Native wheel/touch scrolling, focus, Tab and unrelated widgets remain.
PREVIEW_JS = r"""(() => {
  const installed = Symbol.for("ocr.crop.preview.detail.handlers.v1");
  if (document[installed]) return;
  document[installed] = true;
  const roots = "#ocr-live-source-preview, #ocr-archive-source-preview";
  const detail = "ocr-preview-detail";
  const label = "Original crop detail. Arrow keys scroll; Page Up and Page Down or Space scroll vertically; " +
    "Shift Space scrolls up; Home and End reach opposite corners. Tab leaves this preview.";

  function imageButton(target) {
    if (!(target instanceof Element)) return null;
    const button = target.closest("button");
    if (!button || !button.parentElement?.classList.contains("image-container")) return null;
    const root = button.closest(roots);
    if (!root) return null;
    // Reject toolbar/nested buttons, including any future unrelated child control.
    if (!button.querySelector(":scope > .image-frame > img")) return null;
    return button;
  }

  function viewport(target) {
    const button = imageButton(target);
    return button && button.closest(roots).classList.contains(detail) ? button : null;
  }

  function labelViewport(button) {
    if (viewport(button)) button.setAttribute("aria-label", label);
    // A component update can reuse the button. Never leave our detail-only
    // instructions on Fit, and never remove another component's own label.
    else if (button.getAttribute("aria-label") === label) button.removeAttribute("aria-label");
  }

  function stop(event) {
    event.preventDefault();
    event.stopImmediatePropagation();
  }

  document.addEventListener("click", event => {
    if (viewport(event.target)) stop(event);
  }, true);

  document.addEventListener("keydown", event => {
    const button = viewport(event.target);
    if (!button || document.activeElement !== button) return;
    const activation = event.key === "Enter" || event.key === " ";
    if (event.altKey || event.ctrlKey || event.metaKey) {
      if (activation) stop(event);
      return;
    }
    let left = 0;
    let top = 0;
    switch (event.key) {
      case "ArrowLeft": left = -32; break;
      case "ArrowRight": left = 32; break;
      case "ArrowUp": top = -32; break;
      case "ArrowDown": top = 32; break;
      case "PageUp": top = -button.clientHeight; break;
      case "PageDown": top = button.clientHeight; break;
      case " ": top = (event.shiftKey ? -1 : 1) * button.clientHeight; break;
      case "Home":
        stop(event); button.scrollTo({left: 0, top: 0, behavior: "instant"}); return;
      case "End":
        stop(event); button.scrollTo({left: button.scrollWidth, top: button.scrollHeight, behavior: "instant"}); return;
      case "Enter": stop(event); return;
      default: return;
    }
    stop(event);
    button.scrollBy({left, top, behavior: "instant"});
  }, true);

  document.addEventListener("keyup", event => {
    const button = viewport(event.target);
    if (button && document.activeElement === button && (event.key === "Enter" || event.key === " ")) stop(event);
  }, true);

  document.addEventListener("focusin", event => {
    const button = imageButton(event.target);
    if (button && event.target === button) labelViewport(button);
  }, true);

  document.addEventListener("load", event => {
    if (!(event.target instanceof Element) || event.target.tagName !== "IMG") return;
    const button = imageButton(event.target);
    if (!button) return;
    labelViewport(button);
    if (viewport(button)) button.scrollTo({left: 0, top: 0, behavior: "instant"});
  }, true);
})();"""


def preview_image_update(image, preview_profile="fit"):
    """Return only a bounded PIL output and fixed classes; never resize pixels.

    None clears the image and detail class. Keeping this as a normal Gradio
    value update preserves Image.postprocess, including its failures: it does
    not deliver a separate image URL or authorize any paired UI state.
    """
    from ocr_crop_review_runtime import (
        CropPreviewError, MAX_PREVIEW_PIXELS, MAX_PREVIEW_SIDE, validate_preview_profile,
    )

    profile = validate_preview_profile(preview_profile)
    if image is not None:
        from PIL import Image

        if not isinstance(image, Image.Image) or image.mode != "RGB":
            raise CropPreviewError()
        width, height = image.size
        if (type(width) is not int or type(height) is not int
                or not 1 <= width <= MAX_PREVIEW_SIDE or not 1 <= height <= MAX_PREVIEW_SIDE
                or width * height > MAX_PREVIEW_PIXELS):
            raise CropPreviewError(code="raster_limit")
    import gradio as gr

    return gr.update(value=image, elem_classes=[DETAIL_CLASS] if image is not None and profile != "fit" else [])
