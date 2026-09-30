"""Numeric bridge and installed-Gradio transport, with an inert Node DOM port.

No server/browser, OCR or PDF is started. Node controls exercise the actual JS
against a deliberately small geometry/event double, not real DOM layout,
pointer synthesis or accessibility. Those require later generated browser QA.
"""

import asyncio
import copy
import json
from pathlib import Path
import re
import shutil
import subprocess

import pytest

import ocr_review_crop_uncertainty_editor as editor


# A ceiling, not an expectation: Node can take over 15 s to start on busy
# hosted Windows runners.
_NODE_TIMEOUT = 60


def spec(**changes):
    return editor.editor_value(**{**dict(image_token="image", action_token="action", mode_token="mode",
        mode="annotate", width=800, height=400, requested_bbox=[.25, .5, 799.75, 399.5],
        rectangles=[{"ordinal": 1, "pixel_bbox": [100, 100, 200, 200], "state": 0}]), **changes})


def command(kind, **changes):
    return dict(kind=kind, mode=None, pixel_bbox=None, ordinal=None, **changes) if not changes else {
        **dict(kind=kind, mode=None, pixel_bbox=None, ordinal=None), **changes}


def admit(value):
    return editor.admit_editor(value, image_token="image", action_token="action", mode_token="mode")


@pytest.mark.parametrize("kind,extra", [("mode", {"mode": "browse"}), ("mode", {"mode": "annotate"}),
    ("rectangle", {"pixel_bbox": [0, 0, 800, 400]}), ("annotation", {"ordinal": 128}),
    ("cancel", {}), ("whole_scope", {})])
def test_exact_commands_are_detached_without_authoring_or_mutation(kind, extra):
    value = spec()
    value["command"] = command(kind, **extra)
    before = copy.deepcopy(value)
    result = admit(value)
    assert result == value["command"] and result is not value["command"]
    assert value == before
    result.clear()
    assert value == before


def test_initial_noncommand_and_projection_outside_raster_are_preserved():
    value = spec(requested_bbox=[-.001, -.002, 800.001, 400.002],
                 rectangles=[{"ordinal": 128, "pixel_bbox": [-.001, 1, 20, 22], "state": 2}])
    assert admit(value) is None
    assert value["requested_bbox"] == [-.001, -.002, 800.001, 400.002]
    assert value["rectangles"][0]["pixel_bbox"][0] == -.001


@pytest.mark.parametrize("key", ["image_token", "action_token", "mode_token"])
@pytest.mark.parametrize("token", [None, False, [], "old", "x" * 50000], ids=["none", "bool", "list", "old", "oversized"])
def test_stale_tokens_refuse_before_geometry_inspection(key, token):
    class Poison:
        def __iter__(self):
            pytest.fail("stale capture traversed geometry")

    value = spec()
    value[key] = token
    value["rectangles"] = Poison()
    with pytest.raises(ValueError, match="Crop annotation control is unavailable"):
        admit(value)


@pytest.mark.parametrize("changes", [
    {"image_token": ""}, {"action_token": "private/path"}, {"mode_token": "<script>"},
    {"image_token": "a" * 129}, {"width": True}, {"width": 800.0}, {"height": 0},
    {"width": 1401}, {"mode": "ANNOTATE"}, {"mode": None}, {"rectangles": ()},
    {"requested_bbox": [0, 0, 0, 1]}, {"requested_bbox": [0, 0, float("nan"), 1]},
    {"requested_bbox": [0, False, 1, 1]}, {"requested_bbox": [0, 0, 10 ** 1000, 1]},
    {"rectangles": [{"ordinal": 1, "pixel_bbox": [0, 0, 1, 1], "state": True}]},
    {"rectangles": [{"ordinal": 1, "pixel_bbox": [0, 0, 1, 1], "state": 3}]},
    {"rectangles": [{"ordinal": 129, "pixel_bbox": [0, 0, 1, 1], "state": 0}]},
    {"rectangles": [{"ordinal": 1, "pixel_bbox": [0, 0, 1, 1], "state": 0}] * 129},
])
def test_output_shape_and_scalar_bounds(changes):
    with pytest.raises(ValueError):
        spec(**changes)


@pytest.mark.parametrize("mutation", ["version_bool", "version_float", "extra", "missing", "rect_duplicate", "rect_extra"])
def test_closed_schemas_and_exact_versions(mutation):
    value = spec()
    if mutation == "version_bool":
        value["schema_version"] = True
    elif mutation == "version_float":
        value["schema_version"] = 1.0
    elif mutation == "extra":
        value["reference"] = "never admitted"
    elif mutation == "missing":
        del value["command"]
    elif mutation == "rect_duplicate":
        value["rectangles"] *= 2
    else:
        value["rectangles"][0]["reading"] = "never admitted"
    with pytest.raises(ValueError):
        admit(value)


@pytest.mark.parametrize("payload", [command("unknown"), command("cancel", ordinal=1), command("whole_scope", pixel_bbox=[0, 0, 1, 1]),
    command("mode", mode="bad"), command("annotation", ordinal=True), command("annotation", ordinal=1.0),
    command("annotation", ordinal=0), command("rectangle", pixel_bbox=[-.001, 0, 1, 1]),
    command("rectangle", pixel_bbox=[0, 0, 801, 1]), command("rectangle", pixel_bbox=[0, 0, 1, 401]),
    command("rectangle", pixel_bbox=[0, 0, 1, 0]), command("rectangle", pixel_bbox=[0, 0, 1, 1], mode="annotate")])
def test_unknown_mixed_and_outside_commands_refuse(payload):
    value = spec()
    value["command"] = payload
    with pytest.raises(ValueError):
        admit(value)


def test_byte_bound_after_scalar_preflight_and_signed_zero(monkeypatch):
    value = spec(requested_bbox=[-0.0, 0, 800, 400])
    assert json.dumps(value).find("-0.0") == -1
    monkeypatch.setattr(editor, "MAX_EDITOR_BYTES", 5)
    with pytest.raises(ValueError):
        spec()


def test_key_preflight_does_not_call_unknown_hash_or_equality_again():
    armed = False

    class Unknown:
        def __hash__(self):
            if armed:
                pytest.fail("untrusted key rehashed")
            return hash("command")

        def __eq__(self, _other):
            if armed:
                pytest.fail("untrusted key compared")
            return False

    value = spec()
    del value["command"]
    value[Unknown()] = None
    armed = True
    with pytest.raises(ValueError):
        admit(value)


@pytest.mark.parametrize("identifier", ["private", "#ocr-live-source-preview", "ocr-live-source-preview img", None, [], "x" * 5000])
def test_unknown_image_targets_refuse(identifier):
    with pytest.raises(ValueError):
        editor.build_uncertainty_editor(identifier)


@pytest.mark.parametrize("identifier", [editor.LIVE_PREVIEW_ID, editor.ARCHIVE_PREVIEW_ID])
def test_actual_component_config_and_private_process_api(tmp_path, monkeypatch, identifier):
    pytest.importorskip("gradio")
    import gradio as gr

    monkeypatch.setenv("GRADIO_TEMP_DIR", str(tmp_path))
    received = []
    with gr.Blocks() as app:
        bridge = editor.build_uncertainty_editor(identifier)
        output = gr.JSON()

        def select(value):
            result = admit(value)
            received.append(result)
            return result

        bridge.input(select, bridge, output, api_visibility="private", queue=False)
    event = next(iter(app.fns.values()))
    assert event.api_visibility == "private" and event.queue is False
    assert bridge.value is None and bridge.props == {}
    assert bridge.server_fns == [] and bridge.head is None and bridge.buttons == []
    assert bridge.html_template == editor.EDITOR_HTML and "${" not in bridge.html_template
    assert bridge.js_on_load == editor.EDITOR_JS.replace("__OCR_FIXED_IMAGE_ID__", json.dumps(identifier))
    assert "props.image_elem_id" not in bridge.js_on_load
    value = spec()
    value["command"] = command("rectangle", pixel_bbox=[1, 2, 3, 4])
    result = asyncio.run(app.process_api(event, [value], session_hash="numeric-bridge"))
    assert result["data"][0].root == value["command"] and received == [value["command"]]
    value["action_token"] = "old"
    with pytest.raises(ValueError):
        asyncio.run(app.process_api(event, [value], session_hash="numeric-bridge"))
    assert len(received) == 1


def test_image_postprocess_failure_does_not_return_numeric_image_token(tmp_path, monkeypatch):
    pytest.importorskip("gradio")
    import gradio as gr
    from PIL import Image

    monkeypatch.setenv("GRADIO_TEMP_DIR", str(tmp_path))
    pixels = Image.new("RGB", (2, 2))
    with gr.Blocks() as app:
        image = gr.Image(type="pil", format="png", interactive=False, sources=[], buttons=[],
                         elem_id=editor.LIVE_PREVIEW_ID)
        bridge = editor.build_uncertainty_editor(editor.LIVE_PREVIEW_ID)
        gr.Button().click(lambda: (pixels, spec(width=2, height=2, requested_bbox=[0, 0, 2, 2], rectangles=[])),
                          None, [image, bridge], api_visibility="private")

    def refuse(_self, _value):
        raise RuntimeError("generated image postprocess failure")

    monkeypatch.setattr(gr.Image, "postprocess", refuse)
    try:
        with pytest.raises(gr.exceptions.ComponentProcessingError, match="generated image postprocess failure") as caught:
            asyncio.run(app.process_api(next(iter(app.fns.values())), [], session_hash="undelivered"))
        assert isinstance(caught.value.__cause__, RuntimeError)
        assert bridge.value is None
    finally:
        pixels.close()


def test_script_has_no_pixel_url_authored_value_or_global_storage_port():
    for forbidden in ("currentSrc", ".src", "getImageData", "canvas", "fetch(", "XMLHttpRequest", "localStorage", "sessionStorage",
                      "querySelector('textarea", 'querySelector("textarea', "document.querySelector", "upload("):
        assert forbidden not in editor.EDITOR_JS
    assert re.search(r"\bserver\.", editor.EDITOR_JS) is None
    assert "props.value = captured" in editor.EDITOR_JS and "trigger('input')" in editor.EDITOR_JS
    assert "t.textContent = String(ordinal)" in editor.EDITOR_JS
    assert "innerHTML" not in editor.EDITOR_JS


_HARNESS = r"""
const assert=require('node:assert/strict');
const fs=require('node:fs');
const input=JSON.parse(fs.readFileSync(0,'utf8'));
class Element {
  constructor(tag='div') {
    this.tagName=tag.toUpperCase();this.parentElement=null;this.children=[];this.style={position:''};this.attrs={};
    this.listeners=new Map();this.dataset={};this.isConnected=true;this.clientLeft=0;this.clientTop=0;
    this.scrollLeft=0;this.scrollTop=0;this.clientWidth=400;this.clientHeight=400;this.captured=new Set();
    this.computed={position:'static',transform:'none',objectFit:'scale-down',objectPosition:'50% 50%',
      borderLeftWidth:'0',borderRightWidth:'0',borderTopWidth:'0',borderBottomWidth:'0',
      paddingLeft:'0',paddingRight:'0',paddingTop:'0',paddingBottom:'0'};
    this.box={left:100,top:200,width:400,height:400};
  }
  addEventListener(n,fn){const a=this.listeners.get(n)||[];a.push(fn);this.listeners.set(n,a);}
  removeEventListener(n,fn){this.listeners.set(n,(this.listeners.get(n)||[]).filter(f=>f!==fn));}
  appendChild(n){n.parentElement=this;this.children.push(n);return n;}
  replaceChildren(){this.children=[];}
  remove(){if(this.parentElement)this.parentElement.children=this.parentElement.children.filter(n=>n!==this);this.isConnected=false;}
  setAttribute(k,v){this.attrs[k]=v;}
  getBoundingClientRect(){
    if(this.className==='ocr-uncertainty-plane')return {left:container.box.left+parseFloat(this.style.left||0),
      top:container.box.top+parseFloat(this.style.top||0),width:parseFloat(this.style.width||0),height:parseFloat(this.style.height||0)};
    const r={...this.box}; if(this===image){r.left-=button.scrollLeft;r.top-=button.scrollTop;}return r;
  }
  querySelector(selector){
    if(this===element){assert.equal(selector,'[data-notice]');return notice;}
    if(this===root){assert.equal(selector,'.image-container > button > .image-frame > img');return image;}
    if(this===button){assert.equal(selector,':scope > .image-frame > img');return image;}
    return null;
  }
  querySelectorAll(selector){assert.equal(selector,'[data-command]');return controls;}
  closest(selector){assert.equal(selector,'.image-container > button');for(let n=this;n;n=n.parentElement)if(n===button)return button;return null;}
  contains(n){for(;n;n=n.parentElement)if(n===this)return true;return false;}
  focus(){document.activeElement=this;for(const f of this.listeners.get('focus')||[])f({target:this});}
  setPointerCapture(id){this.captured.add(id);}
  hasPointerCapture(id){return this.captured.has(id);}
  releasePointerCapture(id){this.captured.delete(id);}
  scrollBy(v){assert.equal(v.behavior,'instant');this.scrollLeft+=v.left;this.scrollTop+=v.top;}
}
global.Element=Element;
const root=new Element(),container=root.appendChild(new Element()),button=container.appendChild(new Element('button'));
const image=button.appendChild(new Element()).appendChild(new Element('img'));
image.complete=true;image.naturalWidth=800;image.naturalHeight=400;
for(const forbidden of ['src','currentSrc'])Object.defineProperty(image,forbidden,{get(){throw Error('image URL read');}});
const element=new Element(),notice=new Element('p');
const controls=['browse','annotate','whole_scope','cancel'].map(kind=>{const n=new Element('button');n.dataset.command=kind;return n;});
const document=new Element();document.getElementById=id=>{assert.equal(id,input.id);return root;};
document.createElement=tag=>new Element(tag);document.createElementNS=(ns,tag)=>{assert.equal(ns,'http://www.w3.org/2000/svg');return new Element(tag);};
global.document=document;global.window=new Element();global.getComputedStyle=n=>n.computed;
global.MutationObserver=class{constructor(fn){this.fn=fn;}observe(){}disconnect(){}};
global.ResizeObserver=class{constructor(fn){this.fn=fn;}observe(){}disconnect(){}};
const frames=[];global.requestAnimationFrame=fn=>{frames.push(fn);return frames.length;};global.cancelAnimationFrame=()=>{};
const events=[];const props={value:input.value,...(input.props ?? {image_elem_id:input.id})};let watcher;
const capture="globalThis.api={plane,point,down,move,up,key,capture,changed,layout,dispose,active,ready,draw,emit,bind,"+
  "layer:()=>layer,svg:()=>svg,pending:()=>pending,config:()=>config,gesture:()=>gesture};";
const source=input.script.replace("watch('value',changed); changed();",capture+"watch('value',changed); changed();");
assert.notEqual(source,input.script);
new Function('element','trigger','props','watch',source)(element,n=>events.push(n),props,(key,fn)=>{assert.equal(key,'value');watcher=fn;});
function event(options={}){return {button:0,isPrimary:true,pointerId:1,clientX:160,clientY:350,target:api.layer(),
  altKey:false,ctrlKey:false,metaKey:false,shiftKey:false,prevented:false,stopped:false,
  preventDefault(){this.prevented=true;},stopImmediatePropagation(){this.stopped=true;},...options};}
function acknowledge(change={}){props.value={...input.value,...change,command:null};watcher();}
function detail(){image.computed.objectFit='none';image.box.width=800;image.box.height=400;button.scrollLeft=75;button.scrollTop=30;api.layout();}
const cases={
 fit(){assert.deepEqual(api.plane(),{left:100,top:300,width:400,height:200,sx:.5,sy:.5});assert.deepEqual(api.point(event()),[120,100]);assert.equal(api.point(event({clientY:250})),null);},
 contain(){image.computed.objectFit='contain';assert.deepEqual(api.point(event()),[120,100]);},
 detail(){detail();assert.deepEqual(api.point(event({clientX:160,clientY:250})),[135,80]);assert.equal(api.layer().style.left,'0px');assert.equal(api.layer().style.top,'0px');},
 border(){image.computed.borderLeftWidth='2';image.computed.paddingTop='4';const p=api.plane();assert.equal(p.left,102);assert.equal(p.top,302.5);},
 transformed(){container.computed.transform='scale(2)';assert.equal(api.plane(),null);api.layout();assert.equal(api.layer().hidden,true);},
 unknown_fit(){image.computed.objectFit='cover';assert.equal(api.plane(),null);},
 unknown_position(){image.computed.objectPosition='left top';assert.equal(api.plane(),null);},
 wrong_size(){image.naturalWidth=799;assert.equal(Boolean(api.ready()),false);},
 unloaded(){image.complete=false;assert.equal(Boolean(api.ready()),false);},
 rectangle(){api.down(event());for(let i=0;i<100;i++)api.move(event({clientX:180,clientY:370}));assert.equal(events.length,0);api.up(event({clientX:180,clientY:370}));assert.deepEqual(props.value.command,{kind:'rectangle',mode:null,pixel_bbox:[120,100,160,140],ordinal:null});assert.deepEqual(events,['input']);},
 reverse(){api.down(event({clientX:180,clientY:370}));api.up(event());assert.deepEqual(props.value.command.pixel_bbox,[120,100,160,140]);},
 outside(){api.down(event());api.up(event({clientY:200}));assert.equal(events.length,0);},
 annotation(){api.down(event({clientX:175,clientY:375}));api.up(event({clientX:175,clientY:375}));assert.equal(props.value.command.kind,'annotation');assert.equal(props.value.command.ordinal,1);},
 degenerate(){api.down(event({clientX:110,clientY:310}));api.up(event({clientX:110,clientY:310}));assert.equal(events.length,0);},
 pointer_id(){api.down(event());api.up(event({pointerId:2,clientX:190}));assert.equal(events.length,0);assert.ok(api.gesture());},
 stale_drag(){api.down(event());acknowledge({action_token:'new'});api.up(event({clientX:180,clientY:370}));assert.equal(events.length,0);},
 keyboard(){api.layer().focus();api.key(event({key:'Enter'}));api.key(event({key:'ArrowRight'}));api.key(event({key:'ArrowDown',shiftKey:true}));api.key(event({key:' '}));assert.deepEqual(props.value.command.pixel_bbox,[400,200,401,210]);assert.equal(events.length,1);},
 escape(){api.layer().focus();api.key(event({key:'Enter'}));api.key(event({key:'Escape'}));assert.equal(props.value.command.kind,'cancel');},
 tab(){api.layer().focus();api.key(event({key:'Enter'}));const e=event({key:'Tab'});api.key(e);assert.equal(e.prevented,false);assert.equal(events.length,0);},
 keyboard_detail(){detail();api.layer().focus();const e=event({key:'ArrowDown',shiftKey:true});api.key(e);assert.equal(e.prevented,true);assert.equal(events.length,0);},
 duplicate_mode(){controls[1].listeners.get('click')[0]();assert.equal(events.length,0);},
 request_mode(){controls[0].listeners.get('click')[0]();assert.equal(props.value.command.kind,'mode');assert.equal(props.value.command.mode,'browse');assert.equal(api.config().mode,'annotate');},
 whole_scope(){controls[2].listeners.get('click')[0]();assert.deepEqual(props.value.command,{kind:'whole_scope',mode:null,pixel_bbox:null,ordinal:null});},
 pending(){api.emit('cancel');api.emit('whole_scope');assert.equal(events.length,1);assert.equal(api.pending(),true);acknowledge({action_token:'new'});api.emit('whole_scope');assert.equal(events.length,2);},
 browse(){acknowledge({mode:'browse'});api.down(event());api.up(event({clientX:180,clientY:370}));assert.equal(events.length,0);assert.equal(api.layer().style.pointerEvents,'none');assert.equal(api.layer().tabIndex,-1);},
 browse_scroll(){acknowledge({mode:'browse'});const e=event({key:'ArrowDown',target:button});api.key(e);api.capture(e);assert.equal(e.prevented,false);},
 latent(){for(const type of ['click','dblclick']){const e=event({type,target:image});api.capture(e);assert.equal(e.prevented,true);}for(const key of ['Enter',' ']){const e=event({type:'keydown',key,target:button});api.capture(e);assert.equal(e.prevented,true);}},
 unrelated(){const e=event({type:'keydown',key:'Enter',target:new Element('button')});api.capture(e);assert.equal(e.prevented,false);},
 invalid_update(){acknowledge({width:1401});assert.equal(api.config(),null);assert.equal(api.layer(),null);assert.ok(controls.every(c=>c.disabled));},
 detached(){element.isConnected=false;api.capture(event({type:'click',target:image}));assert.equal(api.layer(),null);assert.ok([...document.listeners.values()].every(a=>a.length===0));},
 render_bounds(){acknowledge({requested_bbox:[-.01,-.02,800.01,400.02]});const r=api.svg().children[0];assert.equal(r.attrs.x,'-0.01');assert.equal(r.attrs.y,'-0.02');assert.ok(api.svg().children.length<=4);}
};
assert.ok(cases[input.case]);cases[input.case]();api.dispose();
process.stdout.write(JSON.stringify({case:input.case,events:events.length,passed:true}));
"""


@pytest.mark.parametrize("identifier", [editor.LIVE_PREVIEW_ID, editor.ARCHIVE_PREVIEW_ID])
@pytest.mark.parametrize("case", ["fit", "contain", "detail", "border", "transformed", "unknown_fit", "unknown_position",
    "wrong_size", "unloaded", "rectangle", "reverse", "outside", "annotation", "degenerate", "pointer_id", "stale_drag",
    "keyboard", "escape", "tab", "keyboard_detail", "duplicate_mode", "request_mode", "whole_scope", "pending", "browse",
    "browse_scroll", "latent", "unrelated", "invalid_update", "detached", "render_bounds"])
def test_actual_script_in_inert_geometry_event_port(identifier, case):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node unavailable for explicitly inert JS control")
    script = editor.EDITOR_JS.replace("__OCR_FIXED_IMAGE_ID__", json.dumps(identifier))
    run = subprocess.run([node, "-e", _HARNESS], input=json.dumps({"script": script, "value": spec(),
                         "id": identifier, "case": case}), text=True, capture_output=True, timeout=_NODE_TIMEOUT,
                         cwd=Path(__file__).resolve().parent)
    assert run.returncode == 0, run.stderr
    assert json.loads(run.stdout) == {"case": case, "events": json.loads(run.stdout)["events"], "passed": True}


@pytest.mark.parametrize("identifier", [editor.LIVE_PREVIEW_ID, editor.ARCHIVE_PREVIEW_ID])
@pytest.mark.parametrize("case", ["rectangle", "keyboard", "request_mode"])
def test_component_script_survives_skip_props_before_lazy_mount(tmp_path, monkeypatch, identifier, case):
    """Real Gradio skip serialization, followed by the named inert JS port.

    This models a value delivered after an HTML prop update while its Tab is
    unmounted. It is not a browser lifecycle or native OCR acceptance test.
    """
    pytest.importorskip("gradio")
    import gradio as gr

    node = shutil.which("node")
    if not node:
        pytest.skip("Node unavailable for explicitly inert JS control")
    monkeypatch.setenv("GRADIO_TEMP_DIR", str(tmp_path))
    monkeypatch.setenv("GRADIO_ANALYTICS_ENABLED", "False")
    with gr.Blocks(analytics_enabled=False) as app:
        bridge = editor.build_uncertainty_editor(identifier)
        gr.Button().click(lambda: gr.skip(), None, bridge, api_visibility="private")
    try:
        result = asyncio.run(app.process_api(next(iter(app.fns.values())), [], session_hash="skip-before-mount"))
        update = result["data"][0]
        # The installed HTML wrapper replaces, rather than merges, nested
        # custom props. Do not hand-repair that actual update in this test.
        props = update.get("props", bridge.props)
        run = subprocess.run([node, "-e", _HARNESS], input=json.dumps({
            "script": bridge.js_on_load, "value": spec(), "props": props,
            "id": identifier, "case": case}), text=True, capture_output=True, timeout=_NODE_TIMEOUT,
            cwd=Path(__file__).resolve().parent)
        assert run.returncode == 0, run.stderr
        assert json.loads(run.stdout)["passed"] is True
    finally:
        app.close()
