"""Presentation-only Save feedback; no session, approval or storage authority.

The client notice acknowledges dispatch, not server admission or publication.
Only a server output can report a checked completion. Keep progress separate
from the live region so the framework's loading overlay cannot dim its text.
"""
from __future__ import annotations

import json


SAVE_PENDING = "Saving and verifying this private revision; completion is not yet confirmed."
SAVE_UNCONFIRMED = (
    "This Save attempt is unconfirmed. Partial or complete data may remain; "
    "inspect the saved catalog before retrying. Draft history was not discarded."
)
_REQUESTED = "Save requested. Waiting or processing; completion is not yet confirmed."
_MOUNT = """
element.setAttribute("role", "status");
element.setAttribute("aria-live", "polite");
element.setAttribute("aria-atomic", "true");
element.addEventListener("ocr-save-requested", (event) => {
    event.stopPropagation();
    props.value = """ + json.dumps(_REQUESTED) + """;
});
"""
_IDS = {"live": "ocr-live-save-status", "archive": "ocr-archive-save-status"}


def build_save_feedback(panel):
    """Build fixed local feedback and a non-authoritative input-preserving hook."""
    import gradio as gr

    if type(panel) is not str or panel not in _IDS:
        raise ValueError("invalid Save feedback panel")
    identifier = _IDS[panel]
    gr.Markdown("Save status — request and last response")
    status = gr.HTML(
        value="No Save request in this view.",
        html_template='<span data-ocr-save-message>{{value}}</span>',
        js_on_load=_MOUNT,
        elem_id=identifier,
        autoscroll=False,
    )
    progress = gr.HTML(
        value="", html_template="{{value}}", js_on_load=None,
        elem_id=identifier + "-progress", min_height=32,
    )
    gr.Markdown(
        "A request or progress indicator is not a save receipt. If the connection "
        "is interrupted or completion is unconfirmed, inspect the saved catalog "
        "before retrying. Use Cancel, then explicitly reload the exact crop and "
        "prepare again if the view is stale; no approval is restored automatically."
    )
    # The existing event owns its captured inputs. Only the HTML's presentation
    # value changes; using its supported props avoids stale same-value renders.
    requested = f"""(...args) => {{
        const root = document.getElementById({json.dumps(identifier)});
        const message = root?.querySelector('[data-ocr-save-message]');
        if (message) message.dispatchEvent(new Event("ocr-save-requested", {{bubbles: true}}));
        return args;
    }}"""
    return status, progress, requested
