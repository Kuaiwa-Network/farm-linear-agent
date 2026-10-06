"""Read-only FairyGUI source previews, explicitly approximate rather than Editor captures."""
import argparse
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import sys
import xml.etree.ElementTree as ET

from PIL import Image, ImageDraw, ImageFont

from .ledger import LedgerError
from .preview_upload import MAX_PIXELS, MAX_SIDE, _plain_path, read_image
from .uploads import _read_regular

MAX_NODES = 5000
MAX_DEPTH = 32
MAX_RENDER_PIXELS = 64_000_000


class MissingArt(LedgerError):
    """An unavailable local asset can be pictured as a gap, never a successful preview."""


def pair(value, default=(0, 0)):
    try:
        result = tuple(float(v) for v in value.split(",")) if value else default
        if len(result) != 2 or not all(math.isfinite(v) and abs(v) <= MAX_SIDE * 4 for v in result):
            raise ValueError()
        return result
    except (TypeError, ValueError):
        raise LedgerError("invalid bounded preview geometry") from None


def dimensions(value):
    w, h = (round(v) for v in value)
    if not (0 < w <= MAX_SIDE and 0 < h <= MAX_SIDE and w * h <= MAX_PIXELS):
        raise LedgerError("preview dimensions exceed limits")
    return w, h


def color(value, default="#ffffffff"):
    value = value or default
    if not re.fullmatch(r"#[0-9a-fA-F]{6}(?:[0-9a-fA-F]{2})?", value):
        raise LedgerError("invalid preview color")
    raw = bytes.fromhex(value[1:])
    return tuple(raw) + (255,) if len(raw) == 3 else (*raw[1:], raw[0])


def nine_slice(source, size, grid):
    w, h = source.size
    try:
        x, y, cw, ch = (int(v) for v in grid.split(","))
    except (ValueError, AttributeError):
        raise LedgerError("invalid nine-slice grid") from None
    if min(x, y) < 0 or min(cw, ch) <= 0 or x + cw > w or y + ch > h:
        raise LedgerError("nine-slice grid exceeds its source image")
    nw, nh = dimensions(size)
    left, top, right, bottom = x, y, w - x - cw, h - y - ch
    if left + right > nw:
        left = round(nw * left / (left + right)); right = nw - left
    if top + bottom > nh:
        top = round(nh * top / (top + bottom)); bottom = nh - top
    sx, sy = (0, x, x + cw, w), (0, y, y + ch, h)
    dx, dy = (0, left, nw - right, nw), (0, top, nh - bottom, nh)
    output = Image.new("RGBA", (nw, nh))
    for row in range(3):
        for col in range(3):
            size = (dx[col+1]-dx[col], dy[row+1]-dy[row])
            if min(size) > 0 and sx[col+1] > sx[col] and sy[row+1] > sy[row]:
                part = source.crop((sx[col], sy[row], sx[col+1], sy[row+1])).resize(size, Image.Resampling.LANCZOS)
                output.alpha_composite(part, (dx[col], dy[row]))
    return output


class Renderer:
    def __init__(self, project_root, *, font=None, controllers=None):
        self.root = Path(project_root)
        _plain_path(self.root)
        if not self.root.is_dir():
            raise LedgerError("preview project root is unavailable")
        self.packages, self.resources, self.hashes, self.xmls = {}, {}, {}, {}
        self.images, self.gaps, self.approximations = {}, [], []
        self.controllers = dict(controllers or {})
        self.used_controllers = set()
        self.nodes = self.pixels = self.xml_bytes = 0
        self.fonts = {}
        self.font = Path(font) if font else self._native_font()
        if self.font:
            _plain_path(self.font)
            self.font_bytes = _read_regular(self.font, 64 << 20)
        else:
            self.font_bytes = None
        assets = self.root / "assets"
        _plain_path(assets)
        manifests = sorted(assets.glob("*/package.xml"))
        if not manifests or len(manifests) > 1000:
            raise LedgerError("preview package inventory is missing or excessive")
        for path in manifests:
            package = self.xml(path)
            identity = package.get("id")
            if not identity or identity in self.packages:
                raise LedgerError("missing or duplicate package identity")
            self.packages[identity] = path.parent.name
            entries = {}
            folders = set()
            for resource in package.findall("resources/*"):
                rid = resource.get("id")
                if resource.tag == "folder":
                    if (not rid or len(rid) > 512 or not rid.startswith("/") or "\\" in rid or ":" in rid
                            or ".." in Path(rid).parts or rid in folders):
                        raise LedgerError("invalid or duplicate package folder metadata")
                    folders.add(rid)
                    continue  # Folder IDs are paths, not ui:// resource identities.
                if not rid or not re.fullmatch(r"[A-Za-z0-9_]{1,128}", rid) or rid in entries:
                    raise LedgerError("missing or duplicate resource identity")
                entries[rid] = resource
            self.resources[identity] = entries
        if sum(len(entries) for entries in self.resources.values()) > 100_000:
            raise LedgerError("preview resource inventory exceeds limits")

    @staticmethod
    def _native_font():
        candidates = ([Path(os.environ.get("WINDIR", "C:/Windows"))/"Fonts/msyh.ttc"] if os.name == "nt" else
                      [Path("/System/Library/Fonts/PingFang.ttc"), Path("/Library/Fonts/Arial Unicode.ttf")]
                      if sys.platform == "darwin" else [Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc")])
        return next((p.resolve() for p in candidates if p.is_file()), None)

    def path(self, raw):
        path = Path(raw)
        _plain_path(path)
        if not path.resolve().is_relative_to(self.root.resolve()):
            raise LedgerError("preview input escapes the project")
        return path

    def xml(self, raw):
        path = self.path(raw)
        if path in self.xmls:
            return self.xmls[path]
        try:
            data = _read_regular(path, 2 << 20)
            self.xml_bytes += len(data)
            if self.xml_bytes > 32 << 20:
                raise LedgerError("preview aggregate XML budget exceeded")
            if b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
                raise LedgerError("preview XML entities are refused")
            value = ET.fromstring(data)
        except (OSError, ET.ParseError):
            raise LedgerError("preview XML is unavailable or malformed") from None
        self.hashes[path.relative_to(self.root).as_posix()] = hashlib.sha256(data).hexdigest()
        self.xmls[path] = value
        return value

    def resource(self, package, rid):
        if package not in self.packages:
            package = next((key for key, name in self.packages.items() if name == package), None)
        if not package or not re.fullmatch(r"[a-z0-9]{8}", package) or rid not in self.resources[package]:
            raise LedgerError("preview resource/package identity is unknown")
        entry = self.resources[package][rid]
        relative = (entry.get("path", "/").lstrip("/") + "/" + (entry.get("name") or "")).lstrip("/")
        if "\\" in relative or Path(relative).is_absolute() or ".." in Path(relative).parts or ":" in relative:
            raise LedgerError("preview resource path is unsafe")
        if len(relative) > 512 or len(Path(relative).parts) > 16:
            raise LedgerError("preview resource path exceeds limits")
        return package, entry, self.path(self.root / "assets" / self.packages[package] / relative)

    def url(self, value, local):
        if not value.startswith("ui://"):
            raise LedgerError("preview resolves only local ui:// resources")
        body = value[5:]
        if "/" in body:
            package, name = body.split("/", 1)
            package = next((key for key, actual in self.packages.items() if actual == package), None)
            matches = [rid for rid, entry in self.resources.get(package, {}).items()
                       if entry.get("name") in (name, name+".xml", name+".png")]
            if len(matches) != 1:
                raise LedgerError("preview named resource is missing or ambiguous")
            return package, matches[0]
        return body[:8] or local, body[8:]

    def canvas(self, size):
        return Image.new("RGBA", self.account(size))

    def account(self, size):
        size = dimensions(size)
        self.pixels += size[0] * size[1]
        if self.pixels > MAX_RENDER_PIXELS:
            raise LedgerError("preview aggregate pixel budget exceeded")
        return size

    def gap(self, label, size):
        self.gaps.append(label)
        result = self.canvas(size)
        draw = ImageDraw.Draw(result)
        draw.rectangle((0, 0, result.width-1, result.height-1), outline="red", width=2)
        draw.line((0, 0, result.width-1, result.height-1), fill="red", width=2)
        return result

    def selected(self, tree, *, scope, top):
        selected = {}
        for ctrl in tree.findall("controller"):
            if not ctrl.get("name") or ctrl.get("name") in selected:
                raise LedgerError("missing or duplicate controller name")
            pages = (ctrl.get("pages") or "").split(",")
            if len(pages) % 2 or len(pages) > 100:
                raise LedgerError("invalid bounded controller pages")
            choices = dict(zip(pages[::2], pages[1::2]))
            if len(choices) != len(pages)//2:
                raise LedgerError("duplicate controller page identity")
            explicit = scope+":"+str(ctrl.get("name"))
            if explicit in self.controllers or top and ctrl.get("name") in self.controllers:
                key = explicit if explicit in self.controllers else ctrl.get("name")
                self.used_controllers.add(key)
                choice = self.controllers[key]
            else:
                try:
                    index = int(ctrl.get("selected", "0"))
                    if not 0 <= index < len(choices):
                        raise ValueError()
                    choice = list(choices)[index]
                except (ValueError, IndexError):
                    raise LedgerError("invalid default controller selection") from None
            if choice not in choices:
                matches = [key for key, name in choices.items() if name == choice]
                if len(matches) != 1:
                    raise LedgerError("unknown preview controller page")
                choice = matches[0]
            selected[ctrl.get("name")] = choice
        return selected

    def attributes(self, node, selected):
        attrs = dict(node.attrib)
        for gear in node:
            if not gear.tag.startswith("gear"):
                continue
            ctrl = gear.get("controller")
            if ctrl not in selected:
                raise LedgerError("preview gear references an unknown controller")
            page = selected[ctrl]
            pages = (gear.get("pages") or "").split(",")
            if gear.tag == "gearDisplay":
                if page not in pages:
                    attrs["visible"] = "false"
                continue
            keys = {"gearXY": "xy", "gearSize": "size", "gearText": "text",
                    "gearColor": "color", "gearIcon": "url", "gearFontSize": "fontSize"}
            if gear.tag not in keys:
                self.gaps.append("unsupported " + gear.tag)
                continue
            values = (gear.get("values") or "").split("|")
            value = values[pages.index(page)] if page in pages and pages.index(page) < len(values) else gear.get("default")
            if value is not None:
                if gear.tag == "gearSize" and len(value.split(",")) == 4:
                    parts = value.split(",")
                    attrs["size"], attrs["scale"] = ",".join(parts[:2]), ",".join(parts[2:])
                elif gear.tag == "gearColor" and "," in value:
                    attrs["color"] = value.split(",", 1)[0]
                    self.approximations.append("gear stroke color not rendered")
                else:
                    attrs[keys[gear.tag]] = value
        return attrs

    def text(self, attrs, size):
        output = self.canvas(size)
        text = attrs.get("text", "")
        if len(text) > 2000:
            raise LedgerError("preview text exceeds limits")
        try:
            font_size = int(attrs.get("fontSize", "30"))
            if not 4 <= font_size <= 256:
                raise ValueError()
        except ValueError:
            raise LedgerError("invalid preview font size") from None
        if any(ord(c) > 127 for c in text) and not self.font_bytes:
            return self.gap("CJK/native font unavailable", size)
        if font_size not in self.fonts:
            if len(self.fonts) >= 16:
                raise LedgerError("preview font-size inventory exceeds limits")
            try:
                self.fonts[font_size] = (ImageFont.truetype(io.BytesIO(self.font_bytes), font_size)
                                        if self.font_bytes else ImageFont.load_default(size=font_size))
            except (OSError, ValueError):
                raise LedgerError("preview font cannot be loaded") from None
        font = self.fonts[font_size]
        draw = ImageDraw.Draw(output)
        lines = []
        for paragraph in text.split("\n"):
            line = ""
            for char in paragraph:
                if attrs.get("singleLine") != "true" and line and draw.textlength(line+char, font=font) > output.width:
                    lines.append(line); line = ""
                line += char
            lines.append(line)
        line_height = font_size + 2
        y = max(0, (output.height - len(lines)*line_height)/2) if attrs.get("vAlign") == "middle" else 0
        try:
            stroke = int(attrs.get("strokeSize", "0"))
            if not 0 <= stroke <= 32:
                raise ValueError()
        except ValueError:
            raise LedgerError("invalid preview text stroke") from None
        for line in lines:
            width = draw.textlength(line, font=font)
            x = max(0, (output.width-width)/2) if attrs.get("align") == "center" else max(0, output.width-width) if attrs.get("align") == "right" else 0
            draw.text((x, y), line, font=font, fill=color(attrs.get("color"), "#000000"), anchor="lt",
                      stroke_width=stroke, stroke_fill=color(attrs.get("strokeColor"), "#000000"))
            y += line_height
        if y > output.height:
            self.approximations.append("text may overflow its fixed box")
        if attrs.get("ubb") == "true" or attrs.get("autoSize", "none").lower() not in ("none", "false"):
            self.approximations.append("rich/automatic text layout approximated")
        return output

    def source_image(self, package, rid):
        package, entry, path = self.resource(package, rid)
        key = (package, rid)
        if key not in self.images:
            try:
                snapshot = read_image(path, self.root)
            except LedgerError:
                raise MissingArt("missing, unhydrated or invalid image: " + path.relative_to(self.root).as_posix()) from None
            self.hashes[path.relative_to(self.root).as_posix()] = snapshot.evidence()["sha256"]
            with Image.open(io.BytesIO(snapshot.data)) as source:
                value = source.convert("RGBA")
            self.pixels += value.width * value.height
            if self.pixels > MAX_RENDER_PIXELS:
                raise LedgerError("preview aggregate pixel budget exceeded")
            self.images[key] = value
        return self.images[key], entry

    def image(self, package, rid, size):
        size = self.account(size)
        try:
            source, entry = self.source_image(package, rid)
        except MissingArt as exc:
            return self.gap(str(exc), size)
        if entry.get("scale") == "9grid":
            return nine_slice(source, size, entry.get("scale9grid"))
        return source.resize(dimensions(size), Image.Resampling.LANCZOS)

    def natural_size(self, node, attrs, package):
        if "size" in attrs:
            return pair(attrs["size"])
        pkg, rid = attrs.get("pkg", package), attrs.get("src")
        if node.tag == "loader" and attrs.get("url"):
            pkg, rid = self.url(attrs["url"], package)
        if node.tag in ("image", "component", "loader") and rid:
            pkg, entry, path = self.resource(pkg, rid)
            if entry.tag == "component":
                return pair(self.xml(path).get("size"))
            try:
                source, _ = self.source_image(pkg, rid)
                return source.size
            except MissingArt as exc:
                self.gaps.append(str(exc))
        else:
            self.gaps.append("natural size unavailable for " + node.tag)
        return (100, 100)  # Visible gap box; never reported as rendered.

    def loader(self, attrs, package, size, stack):
        output = self.canvas(size)
        if not attrs.get("url"):
            return output
        pkg, rid = self.url(attrs["url"], package)
        _, entry, path = self.resource(pkg, rid)
        if entry.tag == "component":
            natural = dimensions(pair(self.xml(path).get("size")))
        elif entry.tag == "image":
            try:
                source, _ = self.source_image(pkg, rid)
                natural = source.size
            except MissingArt as exc:
                return self.gap(str(exc), size)
        else:
            return self.gap("unsupported loader resource: " + entry.tag, size)
        fill = attrs.get("fill", "none")
        if fill == "scaleFree":
            target = size
        elif fill in ("scale", "scaleNoBorder", "matchHeight", "matchWidth", "none"):
            ratios = (size[0]/natural[0], size[1]/natural[1])
            factor = (min(ratios) if fill == "scale" else max(ratios) if fill == "scaleNoBorder"
                      else ratios[1] if fill == "matchHeight" else ratios[0] if fill == "matchWidth" else 1)
            target = dimensions((natural[0]*factor, natural[1]*factor))
        else:
            return self.gap("unsupported loader fill: " + fill, size)
        layer = (self.component(pkg, rid, size=target, stack=stack) if entry.tag == "component"
                 else self.image(pkg, rid, target))
        x = (size[0]-target[0])/2 if attrs.get("align") == "center" else size[0]-target[0] if attrs.get("align") == "right" else 0
        y = (size[1]-target[1])/2 if attrs.get("vAlign") == "middle" else size[1]-target[1] if attrs.get("vAlign") == "bottom" else 0
        output.alpha_composite(layer, (round(x), round(y)))
        return output

    def component(self, package, rid, *, size=None, overrides=None, stack=()):
        package, entry, path = self.resource(package, rid)
        key = (package, rid)
        if key in stack or len(stack) >= MAX_DEPTH:
            raise LedgerError("preview component cycle/depth limit")
        if entry.tag != "component":
            raise LedgerError("preview root must name a component")
        tree = self.xml(path)
        if tree.tag != "component":
            raise LedgerError("preview component XML has the wrong root")
        natural = dimensions(pair(tree.get("size")))
        size = dimensions(size or natural)
        output = self.canvas(size)
        selected = self.selected(tree, scope=package+"/"+rid, top=not stack)
        delta = (size[0]-natural[0], size[1]-natural[1])
        overrides = overrides or {}
        progress = tree.find("ProgressBar")
        if progress is not None:
            try:
                maximum, value = float(progress.get("max", "100")), float(progress.get("value", "0"))
                if not math.isfinite(maximum) or not math.isfinite(value) or maximum <= 0:
                    raise ValueError()
                fraction = max(0, min(1, value/maximum))
            except ValueError:
                raise LedgerError("invalid preview progress value") from None
        for node in tree.findall("displayList/*"):
            self.nodes += 1
            if self.nodes > MAX_NODES:
                raise LedgerError("preview node budget exceeded")
            attrs = self.attributes(node, selected)
            if attrs.get("visible") == "false" or node.tag == "group":
                continue
            if attrs.get("name") == "title" and "title" in overrides:
                attrs["text"] = overrides["title"]
            if attrs.get("name") == "icon" and "icon" in overrides:
                attrs["url"] = overrides["icon"]
            xy = list(pair(attrs.get("xy")))
            wh = list(self.natural_size(node, attrs, package))
            for relation in node.findall("relation"):
                if relation.get("target", ""):
                    self.gaps.append("sibling-target relation is unsupported")
                    continue
                for side in (relation.get("sidePair") or "").split(","):
                    if side == "width-width": wh[0] += delta[0]
                    elif side == "height-height": wh[1] += delta[1]
                    elif side == "right-right": xy[0] += delta[0]
                    elif side == "bottom-bottom": xy[1] += delta[1]
                    elif side == "center-center": xy[0] += delta[0]/2
                    elif side == "middle-middle": xy[1] += delta[1]/2
                    elif side in ("left-left", "top-top"): pass
                    else: self.gaps.append("unsupported relation: " + side)
            if progress is not None and attrs.get("name") == "bar":
                wh[0] *= fraction
                if wh[0] < 1:
                    continue
                self.approximations.append("static progress-bar geometry")
            if progress is not None and attrs.get("name") == "title":
                attrs["text"] = str(round(fraction*100))+"%"
            wh = dimensions(wh)
            if node.tag == "text":
                layer = self.text(attrs, wh)
            elif node.tag == "image":
                layer = self.image(attrs.get("pkg", package), attrs.get("src"), wh)
            elif node.tag == "component":
                button = node.find("Button")
                layer = self.component(attrs.get("pkg", package), attrs.get("src"), size=wh,
                                       overrides=dict(button.attrib) if button is not None else {}, stack=(*stack, key))
            elif node.tag == "loader":
                layer = self.loader(attrs, package, wh, (*stack, key))
            elif node.tag == "graph":
                layer = self.canvas(wh)
                draw = ImageDraw.Draw(layer)
                bounds = (0, 0, wh[0]-1, wh[1]-1)
                method = draw.ellipse if attrs.get("type") in ("ellipse", "eclipse") else draw.rectangle
                try:
                    border = int(attrs.get("lineSize", "0"))
                    if not 0 <= border <= 32:
                        raise ValueError()
                except ValueError:
                    raise LedgerError("invalid preview graph border") from None
                method(bounds, fill=color(attrs.get("fillColor")),
                       outline=color(attrs.get("lineColor"), "#000000") if border else None, width=border or 1)
            elif node.tag == "list":
                layer = self.list(node, attrs, package, wh, (*stack, key))
            else:
                layer = self.gap("unsupported display node: " + node.tag, wh)
            scale = pair(attrs.get("scale"), (1, 1))
            if scale != (1, 1):
                target = dimensions((abs(layer.width*scale[0]), abs(layer.height*scale[1])))
                layer = layer.resize(self.account(target), Image.Resampling.LANCZOS)
                if scale[0] < 0: layer = layer.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
                if scale[1] < 0: layer = layer.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
            try:
                alpha = float(attrs.get("alpha", "1"))
                if not math.isfinite(alpha) or not 0 <= alpha <= 1:
                    raise ValueError()
            except ValueError:
                raise LedgerError("invalid preview opacity") from None
            if alpha < 1:
                layer.putalpha(layer.getchannel("A").point(lambda a: round(a*alpha)))
            if attrs.get("rotation", "0") != "0" or attrs.get("anchor") == "true" or attrs.get("skew") or attrs.get("blend"):
                self.gaps.append("rotation/pivot/skew/blend transform is unsupported")
            if attrs.get("group") or attrs.get("grayed") == "true" or attrs.get("fillMethod"):
                self.gaps.append("group/grayscale/partial-fill effects are unsupported")
            if node.tag in ("image", "loader") and attrs.get("color", "#ffffff").lower() not in ("#ffffff", "#ffffffff"):
                self.gaps.append("image tint is unsupported")
            output.alpha_composite(layer, tuple(round(v) for v in xy))
        if tree.find("transition") is not None:
            self.approximations.append("transitions are not simulated")
        return output

    def list(self, node, attrs, package, size, stack):
        output = self.canvas(size)
        items = node.findall("item")
        if len(items) > 200:
            raise LedgerError("preview list item count exceeds limits")
        layout = attrs.get("layout", "col")
        if layout not in ("col", "row", "flow_hz"):
            return self.gap("unsupported list layout: " + layout, size)
        try:
            col_gap, row_gap = int(attrs.get("colGap", "0")), int(attrs.get("lineGap", "0"))
            if not 0 <= col_gap <= MAX_SIDE or not 0 <= row_gap <= MAX_SIDE:
                raise ValueError()
        except ValueError:
            raise LedgerError("invalid preview list spacing") from None
        x = y = row_height = 0
        for item in items:
            pkg, rid = self.url(item.get("url") or attrs.get("defaultItem", ""), package)
            layer = self.component(pkg, rid, overrides=dict(item.attrib), stack=stack)
            if layout == "flow_hz" and x and x+layer.width > output.width:
                x = 0; y += row_height+row_gap; row_height = 0
            output.alpha_composite(layer, (x, y))
            if layout == "col": y += layer.height+row_gap
            else: x += layer.width+col_gap
            row_height = max(row_height, layer.height)
        if attrs.get("virtual") == "true" or attrs.get("scroll"):
            self.approximations.append("list uses only recorded static items")
        if attrs.get("selectionController") or attrs.get("pageController") or any(item.get("controllers") for item in items):
            self.gaps.append("list item/controller selection is unsupported")
        return output

    def verify_inputs(self):
        for relative, digest in self.hashes.items():
            path = self.path(self.root / relative)
            current = hashlib.sha256(_read_regular(path, 20 << 20)).hexdigest()
            if current != digest:
                raise LedgerError("preview source changed during rendering")
        if self.font and hashlib.sha256(_read_regular(self.font, 64 << 20)).hexdigest() != hashlib.sha256(self.font_bytes).hexdigest():
            raise LedgerError("preview font changed during rendering")

    def render(self, package, component, output_root):
        output_root = Path(output_root)
        _plain_path(output_root)
        if (output_root.exists() or output_root.resolve().is_relative_to(self.root.resolve())
                or self.root.resolve().is_relative_to(output_root.resolve())):
            raise LedgerError("preview output must be a new directory outside source roots")
        package, entry, _ = self.resource(package, component)
        image = self.component(package, component)
        if set(self.controllers) != self.used_controllers:
            raise LedgerError("preview controller selection names no rendered controller")
        self.verify_inputs()
        from PIL import __version__ as pillow_version
        report = {"kind": "approximate-source-preview", "status": "gap" if self.gaps else "rendered",
                  "package": self.packages[package], "package_id": package,
                  "component_id": component, "component": entry.get("name"),
                  "pixels": {"width": image.width, "height": image.height}, "controllers": self.controllers,
                  "source_hashes": self.hashes, "gaps": sorted(set(self.gaps)),
                  "approximations": sorted(set(self.approximations)),
                  "font": {"name": self.font.name, "sha256": hashlib.sha256(self.font_bytes).hexdigest()}
                          if self.font_bytes else {"name": "Pillow ASCII default", "sha256": None},
                  "pillow": pillow_version, "tool_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
        encoded = io.BytesIO()
        image.save(encoded, format="PNG")
        data = encoded.getvalue()
        if len(data) > 20 << 20:
            raise LedgerError("preview PNG exceeds the upload byte limit")
        report["preview_sha256"] = hashlib.sha256(data).hexdigest()
        evidence = json.dumps(report, ensure_ascii=False, indent=2)+"\n"
        if len(evidence.encode("utf-8")) > 2 << 20:
            raise LedgerError("preview evidence exceeds the report limit")
        output_root.mkdir(parents=True)
        _plain_path(output_root)
        with (output_root/"preview.png").open("xb") as stream:
            stream.write(data)
        with (output_root/"report.json").open("x", encoding="utf-8") as stream:
            stream.write(evidence)
        return report


def main(argv=None):
    parser = argparse.ArgumentParser(description="Approximate read-only FairyGUI source preview; no Editor/Unity")
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--package", required=True, help="actual package ID or directory name")
    parser.add_argument("--component", required=True, help="actual component resource ID")
    parser.add_argument("--output-root", required=True, help="new absolute private directory outside project roots")
    parser.add_argument("--font", help="prepared native CJK font; no download or installation")
    parser.add_argument("--controller", action="append", default=[], help="NAME=PAGE or PACKAGE_ID/RESOURCE_ID:NAME=PAGE")
    args = parser.parse_args(argv)
    try:
        selections = {}
        if len(args.controller) > 50:
            raise LedgerError("too many preview controller selections")
        for item in args.controller:
            name, sep, value = item.partition("=")
            if not sep or not name or not value or len(item) > 256 or name in selections:
                raise LedgerError("invalid/duplicate preview controller selection")
            selections[name] = value
        renderer = Renderer(args.project_root, font=args.font, controllers=selections)
        result = renderer.render(args.package, args.component, args.output_root)
        print(json.dumps({key: result[key] for key in ("kind", "status", "package_id", "component_id", "pixels",
                                                       "preview_sha256", "gaps", "approximations")}, ensure_ascii=True))
        return 0 if result["status"] == "rendered" else 2
    except (LedgerError, OSError, ValueError) as exc:
        # No absolute source/font/output path or untrusted file content in errors.
        print("preview failed: "+(str(exc) if isinstance(exc, LedgerError) else type(exc).__name__), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
