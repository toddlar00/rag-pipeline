"""Installed Image/update controls plus an inert Node event-dispatch harness.

No browser, server, PDF, OCR or model is started. The mock DOM tests do not prove
browser layout, native key synthesis or assistive-technology behavior; those
remain explicit real-browser acceptance checks for the pinned Gradio frontend.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

import ocr_review_crop_preview_ui as preview
from ocr_crop_review_runtime import CropPreviewError
from test_review_ocr_execution_cli import launch_case  # noqa: F401
from test_review_ocr_crop_packs_cli import archive_launch  # noqa: F401
from tools import review_ocr


def test_profile_choices_and_ids_are_fixed():
    assert preview.PREVIEW_CHOICES == [("Fit", "fit"), ("288 DPI", "dpi288"), ("576 DPI", "dpi576")]
    assert preview.LIVE_PREVIEW_ID == "ocr-live-source-preview"
    assert preview.ARCHIVE_PREVIEW_ID == "ocr-archive-source-preview"


@pytest.mark.parametrize("profile", ["fit", "dpi288", "dpi576"])
@pytest.mark.parametrize("size", [(311, 28), (28, 311), (1400, 1400), (1, 1)])
def test_update_preserves_original_rgb_pixels_and_uses_only_fixed_classes(profile, size):
    from PIL import Image

    image = Image.new("RGB", size, (13, 97, 211))
    update = preview.preview_image_update(image, profile)
    assert update == {"__type__": "update", "value": image,
                      "elem_classes": [] if profile == "fit" else [preview.DETAIL_CLASS]}
    assert update["value"] is image and image.size == size
    assert image.getpixel((size[0] - 1, size[1] - 1)) == (13, 97, 211)


@pytest.mark.parametrize("profile", ["fit", "dpi288", "dpi576"])
def test_null_image_clears_detail_mode(profile):
    assert preview.preview_image_update(None, profile) == {"__type__": "update", "value": None, "elem_classes": []}


@pytest.mark.parametrize("profile", [None, True, 288, [], {}, "FIT", "288", "dpi144", "dpi576 ", "x" * 10000])
def test_invalid_profile_is_rejected_before_touching_image(profile):
    class Untouchable:
        @property
        def mode(self):
            raise AssertionError("image inspected before profile admission")

    with pytest.raises(CropPreviewError) as result:
        preview.preview_image_update(Untouchable(), profile)
    assert result.value.code == "scope_validation"


@pytest.mark.parametrize("image", ["private.png", "https://example.invalid/image", Path("private.png"), b"RGB", {}, []])
def test_paths_urls_and_other_image_providers_are_not_admitted(image):
    with pytest.raises(CropPreviewError, match="original crop preview unavailable or inputs changed"):
        preview.preview_image_update(image, "dpi288")


@pytest.mark.parametrize("mode", ["L", "RGBA", "P", "CMYK"])
def test_update_never_implicitly_converts_image_modes(mode):
    from PIL import Image

    with pytest.raises(CropPreviewError):
        preview.preview_image_update(Image.new(mode, (2, 2)), "dpi576")


@pytest.mark.parametrize("size", [(0, 1), (1, 0), (1401, 1), (1, 1401)])
def test_update_refuses_outside_raster_bounds_without_downscaling(size):
    from PIL import Image

    with pytest.raises(CropPreviewError) as result:
        preview.preview_image_update(Image.new("RGB", size), "fit")
    assert result.value.code == "raster_limit"


@pytest.mark.parametrize("image_id", [preview.LIVE_PREVIEW_ID, preview.ARCHIVE_PREVIEW_ID])
@pytest.mark.parametrize("profile", ["fit", "dpi288", "dpi576"])
def test_real_image_postprocessing_keeps_pixels_and_private_output_configuration(tmp_path, monkeypatch, image_id, profile):
    import gradio as gr
    from PIL import Image

    monkeypatch.setenv("GRADIO_TEMP_DIR", str(tmp_path))
    pixels = Image.new("RGB", (31, 7), (19, 43, 131))
    pixels.putpixel((30, 6), (211, 3, 89))
    with gr.Blocks() as app:
        output = gr.Image(type="pil", format="png", image_mode="RGB", interactive=False,
                          sources=[], buttons=[], show_label=False, elem_id=image_id,
                          label="Original source preview")
        button = gr.Button("Synthetic output")
        button.click(lambda: preview.preview_image_update(pixels, profile), None, output,
                     api_visibility="private")
    event = next(iter(app.fns.values()))
    assert event.postprocess is True and event.api_visibility == "private"
    state = app.state_holder["synthetic-preview"]

    async def scenario():
        result = await app.process_api(event, [], state=state, session_hash="synthetic-preview")
        item = result["data"][0]
        assert item["elem_classes"] == ([] if profile == "fit" else [preview.DETAIL_CLASS])
        with Image.open(item["value"]["path"]) as stored:
            assert stored.mode == "RGB" and stored.size == pixels.size and stored.tobytes() == pixels.tobytes()
        current = state.blocks_config.blocks[output._id]
        assert current.elem_id == image_id and current.label == "Original source preview"
        assert current.interactive is False and current.sources == current.buttons == []
        assert current.show_label is False and current.type == "pil" and current.format == "png"

    asyncio.run(scenario())


def test_failed_real_image_postprocess_does_not_return_update_or_companion_token(tmp_path, monkeypatch):
    import gradio as gr
    from PIL import Image

    monkeypatch.setenv("GRADIO_TEMP_DIR", str(tmp_path))
    pixels = Image.new("RGB", (2, 2))
    with gr.Blocks() as app:
        output = gr.Image(type="pil", format="png", sources=[], buttons=[], interactive=False)
        token = gr.Textbox(visible=False)
        gr.Button().click(lambda: (preview.preview_image_update(pixels, "dpi576"), "not-delivered"),
                          None, [output, token], api_visibility="private")
    event = next(iter(app.fns.values()))

    def fail(_self, _image):
        raise RuntimeError("synthetic postprocess refusal")

    monkeypatch.setattr(gr.Image, "postprocess", fail)
    with pytest.raises(RuntimeError, match="synthetic postprocess refusal"):
        asyncio.run(app.process_api(event, [], session_hash="failed-preview"))


def test_css_is_scoped_natural_size_and_bounded_without_changing_fit():
    rules = [rule.strip() for rule in preview.PREVIEW_CSS.split("}") if rule.strip()]
    assert len(rules) == 6
    for rule in rules:
        assert rule.startswith(preview._ROOT_SELECTOR)
        assert "#ocr-live-source-preview" in rule and "#ocr-archive-source-preview" in rule
        assert ".ocr-preview-detail" in rule
    assert "max-height: min(60vh, 640px)" in preview.PREVIEW_CSS
    assert "overflow: auto" in preview.PREVIEW_CSS and "width: max-content" in preview.PREVIEW_CSS
    assert "max-width: none; max-height: none; object-fit: none" in preview.PREVIEW_CSS
    assert "scale(" not in preview.PREVIEW_CSS and "url(" not in preview.PREVIEW_CSS
    assert ".svelte-" not in preview.PREVIEW_CSS
    assert preview.PREVIEW_CSS.count("border-radius: 0") == 2
    assert preview._ROOT_SELECTOR + ":focus-within" in preview.PREVIEW_CSS
    assert "outline-offset: 2px" in preview.PREVIEW_CSS and "outline-offset: -" not in preview.PREVIEW_CSS
    assert "outline: none; box-shadow: none" in preview.PREVIEW_CSS


def test_default_launcher_does_not_import_or_install_preview_script(launch_case, monkeypatch):  # noqa: F811
    monkeypatch.setitem(sys.modules, "ocr_review_crop_preview_ui", None)
    assert review_ocr.main(launch_case.args) == 0
    assert "css" not in launch_case.launches[0] and "js" not in launch_case.launches[0]


@pytest.mark.parametrize("mode", ["live", "archive", "both"])
def test_opt_in_launcher_installs_only_fixed_preview_script_and_preserves_security(archive_launch, mode):  # noqa: F811
    c = archive_launch
    args = list(c.args)
    if mode in {"live", "both"}:
        args.append("--enable-ocr-execution")
    if mode in {"archive", "both"}:
        args += ["--crop-review-pack-dir", str(c.pack_root)]
    assert review_ocr.main(args) == 0
    options = c.launches[0]
    assert options["css"] == preview.PREVIEW_CSS and options["js"] == preview.PREVIEW_JS
    assert options["allowed_paths"] == [] and options["max_file_size"] == 1
    assert options["share"] is options["mcp_server"] is options["inbrowser"] is False
    assert options["strict_cors"] is True and options["server_name"] == "127.0.0.1"
    assert "head" not in options and "css_paths" not in options


# This intentionally implements only the selectors/properties used by the fixed
# script. It exercises JS logic in Node, NOT DOM/CSS or native browser behavior.
_EVENT_HARNESS = r"""
const assert = require('node:assert/strict');
const vm = require('node:vm');
const listeners = new Map();
class Element {
  constructor(tag, parent=null, classes=[], id='') {
    this.tagName=tag; this.parentElement=parent; this.classes=new Set(classes); this.id=id;
    this.classList={contains: name => this.classes.has(name)}; this.attrs={};
    this.clientWidth=100; this.clientHeight=80; this.scrollWidth=300; this.scrollHeight=240;
    this.scrollLeft=0; this.scrollTop=0; this.image=null;
  }
  closest(selector) {
    for (let e=this; e; e=e.parentElement) {
      if (selector==='button' && e.tagName==='BUTTON') return e;
      if (selector==='#ocr-live-source-preview, #ocr-archive-source-preview' &&
          ['ocr-live-source-preview','ocr-archive-source-preview'].includes(e.id)) return e;
    }
    return null;
  }
  querySelector(selector) {
    assert.equal(selector, ':scope > .image-frame > img'); return this.image;
  }
  setAttribute(key, text) { assert.equal(key,'aria-label'); this.attrs[key]=text; }
  getAttribute(key) { assert.equal(key,'aria-label'); return this.attrs[key] ?? null; }
  removeAttribute(key) { assert.equal(key,'aria-label'); delete this.attrs[key]; }
  scrollTo({left,top,behavior}) {
    assert.equal(behavior,'instant');
    this.scrollLeft=Math.max(0,Math.min(left,this.scrollWidth-this.clientWidth));
    this.scrollTop=Math.max(0,Math.min(top,this.scrollHeight-this.clientHeight));
  }
  scrollBy({left,top,behavior}) {this.scrollTo({left:this.scrollLeft+left,top:this.scrollTop+top,behavior});}
}
const document={activeElement:null,addEventListener:(name,fn,capture)=>{
  assert.equal(capture,true); const entries=listeners.get(name)||[]; entries.push(fn); listeners.set(name,entries);
}};
const context=vm.createContext({document,Element});
vm.runInContext(SCRIPT,context);
vm.runInContext(SCRIPT,context);
assert.deepEqual([...listeners.keys()],['click','keydown','keyup','focusin','load']);
for (const entries of listeners.values()) assert.equal(entries.length,1);
function tree(id='ocr-live-source-preview', detail=true) {
  const root=new Element('DIV',null,detail?['ocr-preview-detail']:[],id);
  const container=new Element('DIV',root,['image-container']);
  const button=new Element('BUTTON',container);
  const frame=new Element('DIV',button,['image-frame']);
  const image=new Element('IMG',frame); button.image=image;
  return {root,container,button,frame,image};
}
function emit(type,target,options={}) {
  const event={target,key:'',prevented:0,stopped:0,...options,
    preventDefault(){this.prevented++},stopImmediatePropagation(){this.stopped++}};
  for(const fn of listeners.get(type)||[]) {fn(event);if(event.stopped)break;}
  return event;
}
function blocked(event) {assert.equal(event.prevented,1);assert.equal(event.stopped,1);}
function untouched(event) {assert.equal(event.prevented,0);assert.equal(event.stopped,0);}
for(const id of ['ocr-live-source-preview','ocr-archive-source-preview']) {
  const t=tree(id); document.activeElement=t.button;
  // Pointer, synthetic/AT and native keyboard-generated clicks never reach the
  // simulated downstream delegated selection handler in these detail trees.
  for (const target of [t.button,t.frame,t.image]) blocked(emit('click',target));
  blocked(emit('keydown',t.button,{key:'Enter'})); blocked(emit('keyup',t.button,{key:'Enter'}));
  assert.equal(t.button.scrollTop,0);
  blocked(emit('keydown',t.button,{key:' '})); assert.equal(t.button.scrollTop,80);
  blocked(emit('keyup',t.button,{key:' '})); assert.equal(t.button.scrollTop,80);
  blocked(emit('keydown',t.button,{key:' ',shiftKey:true})); assert.equal(t.button.scrollTop,0);
  blocked(emit('keydown',t.button,{key:'ArrowRight'})); assert.equal(t.button.scrollLeft,32);
  blocked(emit('keydown',t.button,{key:'ArrowLeft'})); assert.equal(t.button.scrollLeft,0);
  blocked(emit('keydown',t.button,{key:'ArrowDown'})); assert.equal(t.button.scrollTop,32);
  blocked(emit('keydown',t.button,{key:'ArrowUp'})); assert.equal(t.button.scrollTop,0);
  blocked(emit('keydown',t.button,{key:'PageDown'})); assert.equal(t.button.scrollTop,80);
  blocked(emit('keydown',t.button,{key:'PageUp'})); assert.equal(t.button.scrollTop,0);
  blocked(emit('keydown',t.button,{key:'End'})); assert.deepEqual([t.button.scrollLeft,t.button.scrollTop],[200,160]);
  blocked(emit('keydown',t.button,{key:'Home'})); assert.deepEqual([t.button.scrollLeft,t.button.scrollTop],[0,0]);
  for (const key of ['Tab','Escape','a','F11']) untouched(emit('keydown',t.button,{key}));
  untouched(emit('keydown',t.button,{key:'Tab',shiftKey:true}));
  untouched(emit('keydown',t.button,{key:'ArrowDown',ctrlKey:true}));
  blocked(emit('keydown',t.button,{key:' ',ctrlKey:true}));
  assert.deepEqual([t.button.scrollLeft,t.button.scrollTop],[0,0]);
  emit('focusin',t.button); assert.match(t.button.attrs['aria-label'],/Tab leaves this preview/);
  t.button.scrollTo({left:200,top:160,behavior:'instant'});
  emit('load',t.image); assert.deepEqual([t.button.scrollLeft,t.button.scrollTop],[0,0]);
  t.button.scrollTo({left:200,top:160,behavior:'instant'});
  emit('focusin',t.button); assert.deepEqual([t.button.scrollLeft,t.button.scrollTop],[200,160]);
  document.activeElement=null; untouched(emit('keydown',t.button,{key:'ArrowDown'}));
  // A profile change removes the class: no stale script authority persists.
  t.root.classes.clear(); document.activeElement=t.button;
  untouched(emit('click',t.image)); untouched(emit('keydown',t.button,{key:'Enter'}));
  emit('load',t.image); assert.deepEqual(t.button.attrs,{});
  t.root.classes.add('ocr-preview-detail'); emit('focusin',t.button);
  assert.match(t.button.attrs['aria-label'],/Tab leaves this preview/);
  t.root.classes.clear(); emit('focusin',t.button); assert.deepEqual(t.button.attrs,{});
  t.button.attrs['aria-label']='Original metadata'; emit('load',t.image); emit('focusin',t.button);
  assert.equal(t.button.attrs['aria-label'],'Original metadata');
}
for(const t of [tree('ocr-live-source-preview',false),tree('unrelated-image')]) {
  document.activeElement=t.button;
  for(const type of ['click','keydown','keyup']) untouched(emit(type,t.image,{key:' '}));
  emit('load',t.image); emit('focusin',t.button); assert.deepEqual(t.button.attrs,{});
}
const t=tree();
const toolbar=new Element('DIV',t.container);
const toolButton=new Element('BUTTON',toolbar);
const nestedButton=new Element('BUTTON',t.frame);
for(const target of [toolButton,nestedButton,new Element('TEXTAREA',t.root),t.root,null,{}]) {
  document.activeElement=target;
  for(const type of ['click','keydown','keyup']) untouched(emit(type,target,{key:'Enter'}));
}
assert(!listeners.has('wheel')&&!listeners.has('pointerdown')&&!listeners.has('touchstart'));
console.log('inert detail-event controls passed; no browser layout claim');
"""


def test_inert_javascript_scoping_activation_scroll_and_fit_controls():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is required for the inert JavaScript control, not for the Python UI")
    script = "const SCRIPT=" + json.dumps(preview.PREVIEW_JS) + ";\n" + _EVENT_HARNESS
    environment = {key: value for key, value in os.environ.items() if not key.upper().startswith("NODE")}
    completed = subprocess.run([node, "-e", script], capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=15, env=environment, check=False)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert completed.stdout.strip() == "inert detail-event controls passed; no browser layout claim"


def test_script_has_no_image_url_state_callback_network_or_polling_access():
    for forbidden in (".src", ".value", "fetch(", "XMLHttpRequest", "WebSocket", "localStorage", "sessionStorage",
                      "innerHTML", "outerHTML", "eval(", "setInterval", "MutationObserver", "__click", "dispatch("):
        assert forbidden not in preview.PREVIEW_JS
    assert "ImagePreview click -> select" in Path(preview.__file__).read_text(encoding="utf-8")
