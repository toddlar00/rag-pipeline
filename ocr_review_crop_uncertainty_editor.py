"""Numeric-only, explicitly enabled crop-uncertainty image-plane control.

This is a Gradio 6.25 presentation/command bridge, not an authoring, geometry,
image-delivery or approval boundary. Owners supply geometry from their retained
validated raster view and only a previously delivered image token. Returned
commands require the owner's current-view admission and real raster mapping.
No authored text, source pixels, image URLs or other component values are read.
"""

from __future__ import annotations

import json
import math
import re

from ocr_review_crop_preview_ui import ARCHIVE_PREVIEW_ID, LIVE_PREVIEW_ID


MAX_EDITOR_BYTES = 32 * 1024
MAX_RECTANGLES = 128
_TOKEN = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_FIELDS = {"schema_version", "image_token", "action_token", "mode_token", "mode",
           "width", "height", "requested_bbox", "rectangles", "command"}
_COMMAND_FIELDS = {"kind", "mode", "pixel_bbox", "ordinal"}


def _fail():
    raise ValueError("Crop annotation control is unavailable; reload the current view.")


def _fields(value, expected):
    if (type(value) is not dict or len(value) != len(expected)
            or any(type(key) is not str for key in value) or set(value) != expected):
        _fail()


def _token(value):
    if type(value) is not str or _TOKEN.fullmatch(value) is None:
        _fail()
    return value


def _integer(value, lower, upper):
    if type(value) is not int or not lower <= value <= upper:
        _fail()
    return value


def _bbox(value, *, width=None, height=None):
    if type(value) is not list or len(value) != 4:
        _fail()
    for number in value:
        if (type(number) not in (int, float) or not -16_777_216 <= number <= 16_777_216
                or not math.isfinite(number)):
            _fail()
    if not value[0] < value[2] or not value[1] < value[3]:
        _fail()
    if width is not None and not (0 <= value[0] < value[2] <= width
                                  and 0 <= value[1] < value[3] <= height):
        _fail()
    # Stored source boxes can project outside the raster at a tolerance-rounded
    # edge. Do not clamp the overlay or round-trip it back into source geometry.
    return [0.0 if number == 0 else float(number) for number in value]


def _command(value, *, width, height):
    _fields(value, _COMMAND_FIELDS)
    kind = value["kind"]
    if type(kind) is not str or kind not in ("mode", "rectangle", "annotation", "cancel", "whole_scope"):
        _fail()
    mode, rectangle, ordinal = value["mode"], value["pixel_bbox"], value["ordinal"]
    if kind == "mode":
        if type(mode) is not str or mode not in ("browse", "annotate") or rectangle is not None or ordinal is not None:
            _fail()
    elif kind == "rectangle":
        rectangle = _bbox(rectangle, width=width, height=height)
        if mode is not None or ordinal is not None:
            _fail()
    elif kind == "annotation":
        ordinal = _integer(ordinal, 1, MAX_RECTANGLES)
        if mode is not None or rectangle is not None:
            _fail()
    elif mode is not None or rectangle is not None or ordinal is not None:
        _fail()
    return {"kind": kind, "mode": mode, "pixel_bbox": rectangle, "ordinal": ordinal}


def _value(value):
    _fields(value, _FIELDS)
    _integer(value["schema_version"], 1, 1)
    for key in ("image_token", "action_token", "mode_token"):
        _token(value[key])
    if type(value["mode"]) is not str or value["mode"] not in ("browse", "annotate"):
        _fail()
    width, height = _integer(value["width"], 1, 1400), _integer(value["height"], 1, 1400)
    requested = _bbox(value["requested_bbox"])
    rectangles = value["rectangles"]
    if type(rectangles) is not list or len(rectangles) > MAX_RECTANGLES:
        _fail()
    checked, last = [], 0
    for row in rectangles:
        _fields(row, {"ordinal", "pixel_bbox", "state"})
        ordinal = _integer(row["ordinal"], 1, MAX_RECTANGLES)
        if ordinal <= last:
            _fail()
        last = ordinal
        checked.append({"ordinal": ordinal, "pixel_bbox": _bbox(row["pixel_bbox"]),
                        "state": _integer(row["state"], 0, 2)})
    command = None if value["command"] is None else _command(value["command"], width=width, height=height)
    result = {"schema_version": 1, "image_token": value["image_token"], "action_token": value["action_token"],
              "mode_token": value["mode_token"], "mode": value["mode"], "width": width, "height": height,
              "requested_bbox": requested, "rectangles": checked, "command": command}
    # All individual strings, arrays, numbers and shapes were bounded first.
    if len(json.dumps(result, ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode()) > MAX_EDITOR_BYTES:
        _fail()
    return result


def editor_value(*, image_token, action_token, mode_token, mode, width, height,
                 requested_bbox, rectangles):
    """Detach a server-owned numeric display projection; None disables instead.

    State ordinals are 0 unresolved, 1 resolved and 2 dismissed. This function
    validates only the bridge shape, not source/raster authenticity or delivery.
    """
    return _value(dict(schema_version=1, image_token=image_token, action_token=action_token,
        mode_token=mode_token, mode=mode, width=width, height=height,
        requested_bbox=requested_bbox, rectangles=rectangles, command=None))


def admit_editor(value, *, image_token, action_token, mode_token):
    """Check captured tokens BEFORE client geometry; return only the command.

    Expected tokens come from the server session, never another browser field.
    The caller must still check mode/current source/delivery and map rectangle
    commands using its retained raster view, not the echoed display geometry.
    None is a non-command. This does not rotate tokens or confer any authority.
    """
    _fields(value, _FIELDS)
    for key, expected in (("image_token", image_token), ("action_token", action_token), ("mode_token", mode_token)):
        _token(expected)
        if type(value[key]) is not str or value[key] != expected:
            _fail()
    return _value(value)["command"]


EDITOR_HTML = """
<div class="ocr-uncertainty-controls">
  <div class="ocr-uncertainty-modes" role="group" aria-label="Source image interaction">
    <button type="button" data-command="browse">Browse</button>
    <button type="button" data-command="annotate">Annotate uncertainty</button>
    <button type="button" data-command="whole_scope">Select entire requested crop</button>
    <button type="button" data-command="cancel">Clear pending selection</button>
  </div>
  <p data-notice role="status" aria-live="polite">Open a fresh crop to enable annotation.</p>
  <p>In Annotate mode, drag a rectangle on the image. For keyboard selection,
  focus the image overlay, use arrow keys (Shift for ten pixels), and press
  Enter or Space at each corner. Escape cancels; Tab leaves the overlay.
  Selection is pending until you explicitly apply an authoring action.</p>
</div>
"""

EDITOR_CSS = """
.ocr-uncertainty-modes { display:flex; flex-wrap:wrap; gap:.4rem; }
button { padding:.4rem .65rem; border:1px solid var(--border-color-primary,#777);
  border-radius:4px; background:var(--button-secondary-background-fill,#eee); }
button[aria-pressed="true"] { outline:2px solid var(--color-accent,#2563eb); }
button:disabled { opacity:.6; }
p { margin:.4rem 0; font-size:.9rem; }
"""

# Only these fixed ImagePreview descendants are inspected. The sibling overlay
# does not enter ImagePreview's button; its keyboard events cannot activate it.
# No URLs, source pixels, text/value fields, storage or global sessions are read.
EDITOR_JS = r"""
(() => {
  'use strict';
  const imageId = __OCR_FIXED_IMAGE_ID__;
  if (!['ocr-live-source-preview', 'ocr-archive-source-preview'].includes(imageId)) return;
  const fixedRoot = () => document.getElementById(imageId);
  const notice = element.querySelector('[data-notice]');
  const controls = element.querySelectorAll('[data-command]');
  const ns = 'http://www.w3.org/2000/svg';
  let config = null, pending = false, gesture = null, cursor = null, first = null;
  let root = null, container = null, button = null, img = null, layer = null, svg = null;
  let oldPosition = null, observer = null, sizeObserver = null, frame = 0, disposed = false;
  const stop = e => { e.preventDefault(); e.stopImmediatePropagation(); };
  const finite = n => typeof n === 'number' && Number.isFinite(n) && Math.abs(n) <= 16777216;
  const box = a => Array.isArray(a) && a.length === 4 && a.every(finite) && a[0] < a[2] && a[1] < a[3];
  const keys = (v, names) => v && Object.getPrototypeOf(v) === Object.prototype &&
    Object.keys(v).length === names.length && names.every(k => Object.hasOwn(v,k));
  const token = v => typeof v === 'string' && /^[A-Za-z0-9_-]{1,128}$/.test(v);
  function valid(v) {
    if (!keys(v,['schema_version','image_token','action_token','mode_token','mode','width','height','requested_bbox','rectangles','command']) ||
        v.schema_version !== 1 || !token(v.image_token) || !token(v.action_token) || !token(v.mode_token) ||
        !['browse','annotate'].includes(v.mode) || !Number.isInteger(v.width) || !Number.isInteger(v.height) ||
        v.width < 1 || v.height < 1 || v.width > 1400 || v.height > 1400 || !box(v.requested_bbox) ||
        !Array.isArray(v.rectangles) || v.rectangles.length > 128 || v.command !== null) return false;
    let last = 0;
    return v.rectangles.every(r => {
      if (!keys(r,['ordinal','pixel_bbox','state']) || !Number.isInteger(r.ordinal) || r.ordinal <= last ||
          r.ordinal > 128 || ![0,1,2].includes(r.state) || !box(r.pixel_bbox)) return false;
      last = r.ordinal; return true;
    });
  }
  const clone = v => JSON.parse(JSON.stringify(v));
  function message(text) { if (notice) notice.textContent = text; }
  function same(a,b) { return a && b && a.image_token === b.image_token && a.action_token === b.action_token && a.mode_token === b.mode_token; }
  function emit(kind, extra = {}) {
    if (!config || pending || !ready()) return;
    const captured = clone(config);
    pending = true; gesture = null; first = null;
    captured.command = {kind, mode:null, pixel_bbox:null, ordinal:null, ...extra};
    props.value = captured;
    trigger('input');
    message('Selection request sent. Wait for the current view response; no authoring is applied yet.');
    draw();
  }
  function node(tag, attrs) {
    const n = document.createElementNS(ns,tag);
    for (const [key,value] of Object.entries(attrs)) n.setAttribute(key,String(value));
    return n;
  }
  function rect(bounds, stroke, dash = '', ordinal = null) {
    const r = node('rect',{x:bounds[0],y:bounds[1],width:bounds[2]-bounds[0],height:bounds[3]-bounds[1],
      fill:'none',stroke,'stroke-width':2,'vector-effect':'non-scaling-stroke','pointer-events':'none'});
    if (dash) r.setAttribute('stroke-dasharray',dash);
    svg.appendChild(r);
    if (ordinal !== null) {
      const t = node('text',{x:bounds[0]+2,y:bounds[1]+13,fill:stroke,'font-size':12,
        'font-family':'sans-serif','paint-order':'stroke',stroke:'white','stroke-width':2,'pointer-events':'none'});
      t.textContent = String(ordinal); svg.appendChild(t);
    }
  }
  function ready() {
    return config && img && img.isConnected && img.complete && img.naturalWidth === config.width &&
      img.naturalHeight === config.height && fixedRoot() === root && layer && button && container;
  }
  function plane() {
    if (!ready()) return null;
    const r = img.getBoundingClientRect(), style = getComputedStyle(img);
    // Qualified component uses untransformed, centered contain (Fit) or none
    // (detail). Unknown transforms/object positioning refuse, not approximate.
    if (style.transform !== 'none' || !['contain','none','fill','scale-down'].includes(style.objectFit) ||
        !['50% 50%','center center'].includes(style.objectPosition)) return null;
    if ([img.parentElement,button,container,root].some(n=>getComputedStyle(n).transform!=='none')) return null;
    const metrics = ['borderLeftWidth','borderRightWidth','borderTopWidth','borderBottomWidth',
      'paddingLeft','paddingRight','paddingTop','paddingBottom'].map(k=>Number.parseFloat(style[k]) || 0);
    if (metrics.some(v=>!Number.isFinite(v) || v < 0)) return null;
    const [bl,br,bt,bb,pl,pr,pt,pb] = metrics;
    let w=r.width-bl-br-pl-pr, h=r.height-bt-bb-pt-pb;
    if (!(w > 0 && h > 0)) return null;
    let sx=w/config.width, sy=h/config.height;
    if (style.objectFit === 'contain' || style.objectFit === 'scale-down') {
      sx=sy=Math.min(sx,sy,...(style.objectFit === 'scale-down' ? [1] : []));
    } else if (style.objectFit === 'none') sx=sy=1;
    const width=config.width*sx, height=config.height*sy;
    return {left:r.left+bl+pl+(w-width)/2,top:r.top+bt+pt+(h-height)/2,width,height,sx,sy};
  }
  function layout() {
    if (!layer || !config) return;
    const p=plane();
    if (!p) { layer.hidden=true; return; }
    const b=button.getBoundingClientRect(), c=container.getBoundingClientRect();
    // Clip to the actual button scroll viewport, excluding scrollbars/borders.
    const left=Math.max(p.left,b.left+button.clientLeft), top=Math.max(p.top,b.top+button.clientTop);
    const right=Math.min(p.left+p.width,b.left+button.clientLeft+button.clientWidth);
    const bottom=Math.min(p.top+p.height,b.top+button.clientTop+button.clientHeight);
    if (!(right>left && bottom>top)) { layer.hidden=true; return; }
    layer.hidden=false;
    Object.assign(layer.style,{left:(left-c.left-container.clientLeft+container.scrollLeft)+'px',
      top:(top-c.top-container.clientTop+container.scrollTop)+'px',width:(right-left)+'px',height:(bottom-top)+'px'});
    Object.assign(svg.style,{position:'absolute',left:(p.left-left)+'px',top:(p.top-top)+'px',
      width:p.width+'px',height:p.height+'px'});
    svg.setAttribute('viewBox',`0 0 ${config.width} ${config.height}`);
    svg.setAttribute('preserveAspectRatio','none');
  }
  function draw() {
    if (!svg) return;
    svg.replaceChildren();
    if (!config) return;
    rect(config.requested_bbox,'#a16207','5 3');
    for (const r of config.rectangles) rect(r.pixel_bbox,['#dc2626','#15803d','#6b7280'][r.state],r.state===2?'3 3':'',r.ordinal);
    if (gesture) rect([Math.min(gesture.start[0],gesture.end[0]),Math.min(gesture.start[1],gesture.end[1]),
      Math.max(gesture.start[0],gesture.end[0]),Math.max(gesture.start[1],gesture.end[1])],'#2563eb','4 3');
    if (first && cursor) rect([Math.min(first[0],cursor[0]),Math.min(first[1],cursor[1]),
      Math.max(first[0],cursor[0]),Math.max(first[1],cursor[1])],'#2563eb','4 3');
    if (cursor && config.mode==='annotate') {
      const x=cursor[0],y=cursor[1];
      svg.appendChild(node('path',{d:`M ${x-4} ${y} H ${x+4} M ${x} ${y-4} V ${y+4}`,
        stroke:'#2563eb','stroke-width':1,'vector-effect':'non-scaling-stroke','pointer-events':'none'}));
    }
    layer.tabIndex=config.mode==='annotate'&&!pending?0:-1;
    layer.style.pointerEvents=config.mode==='annotate'&&!pending?'auto':'none';
    layer.style.touchAction=config.mode==='annotate'?'none':'auto';
    layout();
  }
  function point(e) {
    const p=plane();
    if (!p || !finite(e.clientX) || !finite(e.clientY)) return null;
    const u=(e.clientX-p.left)/p.sx, v=(e.clientY-p.top)/p.sy;
    return u>=0 && u<=config.width && v>=0 && v<=config.height ? [u,v] : null;
  }
  function active() { return config && config.mode==='annotate' && !pending && ready(); }
  function initialCursor() {
    const b=layer.getBoundingClientRect();
    return point({clientX:b.left+b.width/2,clientY:b.top+b.height/2}) || [config.width/2,config.height/2];
  }
  function clearTentative() { gesture=null; first=null; draw(); }
  function down(e) {
    if (!active() || e.button!==0 || !e.isPrimary) return;
    const p=point(e); if (!p) return;
    stop(e); layer.focus({preventScroll:true});
    gesture={id:e.pointerId,start:p,end:p,captured:clone(config)}; first=null; cursor=p;
    layer.setPointerCapture(e.pointerId); draw();
  }
  function move(e) {
    if (!gesture || gesture.id!==e.pointerId) return;
    stop(e); const p=point(e); if (p) { gesture.end=p; cursor=p; draw(); }
  }
  function up(e) {
    if (!gesture || gesture.id!==e.pointerId) return;
    stop(e); const g=gesture, p=point(e); gesture=null;
    if (layer.hasPointerCapture(e.pointerId)) layer.releasePointerCapture(e.pointerId);
    if (!p || !same(g.captured,config) || !active()) { draw(); return; }
    const b=[Math.min(g.start[0],p[0]),Math.min(g.start[1],p[1]),Math.max(g.start[0],p[0]),Math.max(g.start[1],p[1])];
    if (b[0]<b[2] && b[1]<b[3]) emit('rectangle',{pixel_bbox:b});
    else {
      const hits=config.rectangles.filter(r=>p[0]>=r.pixel_bbox[0]&&p[0]<=r.pixel_bbox[2]&&p[1]>=r.pixel_bbox[1]&&p[1]<=r.pixel_bbox[3]);
      if (hits.length===1) emit('annotation',{ordinal:hits[0].ordinal});
      else { message('Drag a positive rectangle, or use two keyboard corners.'); draw(); }
    }
  }
  function key(e) {
    if (!active() || e.target!==layer) return;
    if (e.key==='Tab') { clearTentative(); return; }
    if (e.key==='Escape') { stop(e); clearTentative(); emit('cancel'); return; }
    if (e.altKey || e.ctrlKey || e.metaKey) {
      if (e.key==='Enter' || e.key===' ') stop(e);
      return;
    }
    cursor ||= initialCursor();
    const step=e.shiftKey?10:1;
    let dx=0,dy=0;
    if (e.key==='ArrowLeft') dx=-step;
    else if(e.key==='ArrowRight') dx=step;
    else if(e.key==='ArrowUp') dy=-step;
    else if(e.key==='ArrowDown') dy=step;
    else if(e.key==='Enter'||e.key===' ') {
      stop(e);
      if (!first) { first=[...cursor]; message('First corner set. Move to the second corner and press Enter or Space.'); draw(); }
      else {
        const b=[Math.min(first[0],cursor[0]),Math.min(first[1],cursor[1]),Math.max(first[0],cursor[0]),Math.max(first[1],cursor[1])];
        if (b[0]<b[2] && b[1]<b[3]) emit('rectangle',{pixel_bbox:b});
        else message('Both corners must form a positive rectangle.');
      }
      return;
    } else return;
    stop(e); cursor=[Math.max(0,Math.min(config.width,cursor[0]+dx)),Math.max(0,Math.min(config.height,cursor[1]+dy))];
    draw();
    // Bring keyboard corner into the same existing scroll viewport. No zoom,
    // density change, source-box mutation or synthetic pointer command occurs.
    const p=plane(), b=button.getBoundingClientRect();
    if (p) {
      const x=p.left+cursor[0]*p.sx,y=p.top+cursor[1]*p.sy;
      button.scrollBy({left:x<b.left?x-b.left:x>b.left+button.clientWidth?x-b.left-button.clientWidth:0,
        top:y<b.top?y-b.top:y>b.top+button.clientHeight?y-b.top-button.clientHeight:0,behavior:'instant'});
    }
  }
  function schedule() { if (!frame) frame=requestAnimationFrame(()=>{frame=0; bind(); layout();}); }
  function removeLayer() {
    if (button) button.removeEventListener('scroll',schedule);
    if (img) img.removeEventListener('load',schedule);
    sizeObserver?.disconnect(); sizeObserver=null;
    layer?.remove(); layer=null; svg=null;
    if (container && oldPosition!==null && container.style.position==='relative') container.style.position=oldPosition;
    oldPosition=null; container=null; button=null; img=null;
  }
  function bind() {
    if (disposed) return;
    if (!element.isConnected) { dispose(); return; }
    const next=fixedRoot();
    if (next!==root) {
      removeLayer(); observer?.disconnect(); root=next;
      if (root) { observer=new MutationObserver(schedule); observer.observe(root,{childList:true,subtree:true,attributes:true,attributeFilter:['class']}); }
    }
    const nextImage=root?.querySelector('.image-container > button > .image-frame > img');
    if (nextImage===img && layer) return;
    removeLayer();
    if (!nextImage || !config) return;
    img=nextImage; button=img.parentElement.parentElement; container=button.parentElement;
    if (getComputedStyle(container).position==='static') { oldPosition=container.style.position; container.style.position='relative'; }
    layer=document.createElement('div');
    layer.className='ocr-uncertainty-plane'; layer.setAttribute('role','group');
    layer.setAttribute('aria-label','Crop annotation overlay. Arrow keys move a pixel cursor; Enter or Space sets each corner. Escape cancels. Tab leaves.');
    Object.assign(layer.style,{position:'absolute',overflow:'hidden',zIndex:'2',outlineOffset:'-2px'});
    svg=node('svg',{'aria-hidden':'true'}); layer.appendChild(svg); container.appendChild(layer);
    layer.addEventListener('pointerdown',down); layer.addEventListener('pointermove',move); layer.addEventListener('pointerup',up);
    layer.addEventListener('pointercancel',clearTentative); layer.addEventListener('lostpointercapture',clearTentative);
    layer.addEventListener('keydown',key);
    layer.addEventListener('keyup',e=>{if(config?.mode==='annotate'&&(e.key==='Enter'||e.key===' '))stop(e);});
    layer.addEventListener('click',stop); layer.addEventListener('dblclick',stop);
    layer.addEventListener('focus',()=>{cursor ||= initialCursor();draw();});
    layer.addEventListener('blur',clearTentative);
    button.addEventListener('scroll',schedule,{passive:true}); img.addEventListener('load',schedule);
    sizeObserver=new ResizeObserver(schedule); sizeObserver.observe(img); sizeObserver.observe(button);
    draw();
  }
  function changed() {
    const incoming=props.value;
    if (incoming?.command) return; // Our emitted command is not a server ack.
    const previous=config;
    config=valid(incoming)?clone(incoming):null;
    pending=false; gesture=null; first=null;
    if (!same(previous,config)) cursor=null;
    for (const control of controls) {
      const action=control.dataset.command;
      control.disabled=!config || (['whole_scope','cancel'].includes(action)&&config.mode!=='annotate');
      if (action==='browse'||action==='annotate') control.setAttribute('aria-pressed',String(config?.mode===action));
    }
    if (!config) { removeLayer(); message('Open a fresh crop to enable annotation.'); }
    else { bind(); draw(); message(config.mode==='browse'?'Browse mode. Source rectangles are overlays, not OCR corrections.':
      'Annotate mode. Select a rectangle; then choose and explicitly apply an authoring action.'); }
  }
  for (const control of controls) control.addEventListener('click',()=>{
    const kind=control.dataset.command;
    if (kind==='browse'||kind==='annotate') { if(config && config.mode!==kind) emit('mode',{mode:kind}); }
    else emit(kind);
  });
  function suppress(e) {
    const candidate=e.target instanceof Element?e.target.closest('.image-container > button'):null;
    // The existing detail viewer owns Space scrolling. Its capture listener
    // may register after ours; do not swallow that keydown. Keyup and latent
    // click remain suppressed, and annotation-layer Space stays local.
    if (e.type==='keydown' && e.key===' ' && root?.classList?.contains('ocr-preview-detail')) return;
    if (candidate && root?.contains(candidate) && candidate.querySelector(':scope > .image-frame > img') &&
        (e.type==='click'||e.type==='dblclick'||e.key==='Enter'||e.key===' ')) stop(e);
  }
  // Fit also has a latent ImagePreview select handler even with buttons=[].
  // Do not intercept wheel, touch, Tab, arrows or unrelated page controls.
  function capture(e) { if(!element.isConnected) dispose(); else suppress(e); }
  function dispose() {
    disposed=true; if(frame)cancelAnimationFrame(frame);
    observer?.disconnect(); removeLayer(); window.removeEventListener('resize',schedule);
    document.removeEventListener('scroll',schedule,true);
    for(const name of ['click','dblclick','keydown','keyup']) document.removeEventListener(name,capture,true);
  }
  for(const name of ['click','dblclick','keydown','keyup']) document.addEventListener(name,capture,true);
  window.addEventListener('resize',schedule); document.addEventListener('scroll',schedule,true);
  watch('value',changed); changed();
})();
"""


def build_uncertainty_editor(image_elem_id):
    """Build a disabled bridge; owner wires `.input(..., api_visibility='private')`.

    Pair a nonnull editor value with the successful ordinary Image update in
    one event return. Never call this bridge's value a human inspection receipt.
    """
    if type(image_elem_id) is not str or image_elem_id not in (LIVE_PREVIEW_ID, ARCHIVE_PREVIEW_ID):
        _fail()
    import gradio as gr

    # Gradio 6.25 HTML skips can replace custom props before a lazy Tab mounts.
    # This host-selected constant must not depend on those mutable props. Only
    # the two exact IDs admitted above can enter the JSON-encoded script literal.
    script = EDITOR_JS.replace("__OCR_FIXED_IMAGE_ID__", json.dumps(image_elem_id))
    return gr.HTML(value=None, html_template=EDITOR_HTML, css_template=EDITOR_CSS,
        js_on_load=script, apply_default_css=False,
        elem_id=image_elem_id + "-uncertainty", container=False, buttons=[])
