"""Synthetic real PNGs/FGUI sources test visual geometry, gaps and read-only bounds."""
import hashlib
import json
import subprocess
import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from agent.ledger import LedgerError
from agent.ui_preview import Renderer, nine_slice


class UiPreviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="UI preview ")
        self.addCleanup(self.tmp.cleanup)
        self.private = Path(self.tmp.name).resolve()
        self.root = self.private / "source 空间"
        self.root.mkdir()
        self.output = self.private / "render 预览"
        self.enterContext(patch.object(Renderer, "_native_font", return_value=None))
        self.package("One", "pack0001")
        self.component("One", "view", '<image src="art" xy="1,1" size="3,3"/>')

    def package(self, name, identity):
        folder = self.root / "assets" / name
        (folder / "Res").mkdir(parents=True)
        (folder / "package.xml").write_text(
            f'<packageDescription id="{identity}"><resources>'
            '<image id="art" name="red.png" path="/Res/"/>'
            '<component id="view" name="view.xml" path="/" exported="true"/>'
            '<component id="child" name="child.xml" path="/" exported="true"/>'
            '</resources></packageDescription>', encoding="utf-8")
        with Image.new("RGBA", (3, 3), "red") as image:
            image.save(folder / "Res/red.png")
        return folder

    def component(self, package, name, nodes, *, prefix="", suffix="", size="8,8"):
        path = self.root / "assets" / package / (name+".xml")
        path.write_text(f'<component size="{size}">{prefix}<displayList>{nodes}</displayList>{suffix}</component>',
                        encoding="utf-8")
        return path

    def render(self, **options):
        renderer = Renderer(self.root, **options)
        report = renderer.render("One", "view", self.output)
        with Image.open(self.output / "preview.png") as image:
            pixels = image.copy()
        return report, pixels

    def source_map(self):
        return {p.relative_to(self.root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in self.root.rglob("*") if p.is_file()}

    def test_layers_alpha_placement_hashes_and_unicode_paths_without_source_writes(self):
        self.component("One", "view", '<graph size="8,8" fillColor="#00ff00"/>'
                       '<image src="art" xy="1,1" size="3,3" alpha="0.5"/>')
        before = self.source_map()
        report, image = self.render()
        self.assertEqual(image.getpixel((0, 0)), (0, 255, 0, 255))
        self.assertEqual(image.getpixel((2, 2)), (128, 127, 0, 255))
        self.assertEqual(report["status"], "rendered")
        self.assertEqual(report["kind"], "approximate-source-preview")
        self.assertEqual(report["preview_sha256"], hashlib.sha256((self.output/"preview.png").read_bytes()).hexdigest())
        self.assertEqual(before, self.source_map())
        self.assertNotIn(str(self.root), json.dumps(report))
        self.assertEqual(json.loads((self.output/"report.json").read_text(encoding="utf-8")), report)

    def test_native_cli_uses_the_selected_python_and_unicode_paths(self):
        entry = Path(__file__).resolve().parents[1]/"skills/fgui/tools/preview.py"
        run = subprocess.run([sys.executable, "-B", str(entry), "--project-root", str(self.root),
                              "--package", "One", "--component", "view", "--output-root", str(self.output)],
                             capture_output=True, text=True, encoding="utf-8", timeout=30)
        self.assertEqual(run.returncode, 0, run.stderr)
        result = json.loads(run.stdout)
        self.assertEqual(result["kind"], "approximate-source-preview")
        self.assertEqual(result["pixels"], {"width": 8, "height": 8})
        self.assertNotIn(str(self.private), run.stdout)

    def test_native_cli_gap_exit_is_distinct_from_refusal_and_success(self):
        entry = Path(__file__).resolve().parents[1]/"skills/fgui/tools/preview.py"
        args = [sys.executable, "-B", str(entry), "--project-root", str(self.root), "--package", "One",
                "--component", "view", "--output-root", str(self.output)]
        (self.root/"assets/One/Res/red.png").write_bytes(b"unhydrated LFS pointer")
        run = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", timeout=30)
        self.assertEqual(run.returncode, 2, run.stderr)
        self.assertEqual(json.loads(run.stdout)["status"], "gap")
        refused = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", timeout=30)
        self.assertEqual(refused.returncode, 1)
        self.assertNotIn(str(self.private), refused.stderr)

    def test_missing_prepared_dependency_is_a_sanitized_host_gap(self):
        entry = Path(__file__).resolve().parents[1]/"skills/fgui/tools/preview.py"
        run = subprocess.run([sys.executable, "-I", "-S", "-B", str(entry), "--help"],
                             capture_output=True, text=True, encoding="utf-8", timeout=30)
        self.assertEqual(run.returncode, 1)
        self.assertIn("prepared pinned Pillow", run.stderr)
        self.assertNotIn("Traceback", run.stderr)

    def test_nine_slice_preserves_corner_pixels_and_stretches_the_center(self):
        source = Image.new("RGBA", (3, 3), "white")
        source.putpixel((0, 0), (255, 0, 0, 255))
        source.putpixel((2, 0), (0, 255, 0, 255))
        source.putpixel((0, 2), (0, 0, 255, 255))
        result = nine_slice(source, (9, 7), "1,1,1,1")
        self.assertEqual(result.getpixel((0, 0)), (255, 0, 0, 255))
        self.assertEqual(result.getpixel((8, 0)), (0, 255, 0, 255))
        self.assertEqual(result.getpixel((0, 6)), (0, 0, 255, 255))
        self.assertEqual(result.getpixel((4, 3)), (255, 255, 255, 255))
        self.assertEqual(nine_slice(source, (1, 1), "1,1,1,1").size, (1, 1))
        with self.assertRaises(LedgerError):
            nine_slice(source, (10, 10), "3,3,1,1")

    def test_cross_package_component_uses_manifest_identity_and_parent_size_relations(self):
        self.package("Two", "pack0002")
        self.component("Two", "child", '<graph size="4,4" fillColor="#0000ff">'
                       '<relation target="" sidePair="width-width,height-height"/></graph>', size="4,4")
        self.component("One", "view", '<component pkg="pack0002" src="child" size="6,6" xy="1,1"/>')
        report, image = self.render()
        self.assertEqual(image.getpixel((6, 6)), (0, 0, 255, 255))
        self.assertIn("assets/Two/child.xml", report["source_hashes"])
        self.assertEqual(image.getpixel((7, 7)), (0, 0, 0, 0))

    def test_named_controller_page_controls_visibility_position_and_size(self):
        self.component("One", "view", '<image src="art" size="1,1">'
                       '<gearDisplay controller="state" pages="right"/>'
                       '<gearXY controller="state" pages="right" values="4,4" default="0,0"/>'
                       '<gearSize controller="state" pages="right" values="2,2,1,1" default="1,1,1,1"/>'
                       '</image>', prefix='<controller name="state" pages="left,Left,right,Right"/>')
        report, image = self.render(controllers={"state": "Right"})
        self.assertEqual(image.getpixel((4, 4)), (255, 0, 0, 255))
        self.assertEqual(image.getpixel((5, 5)), (255, 0, 0, 255))
        self.assertEqual(image.getpixel((0, 0)), (0, 0, 0, 0))
        self.assertEqual(report["controllers"], {"state": "Right"})

    def test_button_icon_and_empty_title_overrides_change_the_nested_component(self):
        self.component("One", "child", '<loader name="icon" size="4,4" fill="scaleFree"/>'
                       '<text name="title" text="X" fontSize="4" size="4,4"/>', size="4,4")
        self.component("One", "view", '<component src="child" size="4,4">'
                       '<Button title="" icon="ui://pack0001art"/></component>')
        _, image = self.render()
        self.assertEqual({image.getpixel((x, y)) for x in range(4) for y in range(4)}, {(255, 0, 0, 255)})

    def test_static_list_uses_item_overrides_and_places_each_row(self):
        self.component("One", "child", '<loader name="icon" size="4,4"/>', size="4,4")
        self.component("One", "view", '<list size="8,8" defaultItem="ui://One/child" layout="col">'
                       '<item icon="ui://pack0001art"/><item icon="ui://pack0001art"/></list>')
        _, image = self.render()
        self.assertEqual(image.getpixel((1, 1)), (255, 0, 0, 255))
        self.assertEqual(image.getpixel((1, 5)), (255, 0, 0, 255))
        self.assertEqual(image.getpixel((5, 1)), (0, 0, 0, 0))

    def test_progress_bar_fills_only_the_recorded_fraction(self):
        self.component("One", "view", '<graph name="bar" size="8,4" fillColor="#0000ff"/>',
                       suffix='<ProgressBar max="100" value="50"/>')
        report, image = self.render()
        self.assertEqual(image.getpixel((3, 1)), (0, 0, 255, 255))
        self.assertEqual(image.getpixel((4, 1)), (0, 0, 0, 0))
        self.assertIn("static progress-bar geometry", report["approximations"])

    def test_images_without_an_override_use_actual_asset_dimensions(self):
        self.component("One", "view", '<image src="art"/>')
        _, image = self.render()
        self.assertEqual(image.getpixel((2, 2)), (255, 0, 0, 255))
        self.assertEqual(image.getpixel((3, 3)), (0, 0, 0, 0))

    def test_loader_preserves_natural_size_or_aspect_and_obeys_alignment(self):
        self.component("One", "view", '<loader url="ui://pack0001art" size="8,8" align="right" vAlign="bottom"/>')
        _, image = self.render()
        self.assertEqual(image.getpixel((5, 5)), (255, 0, 0, 255))
        self.assertEqual(image.getpixel((4, 4)), (0, 0, 0, 0))
        self.output = self.private/"scaled"
        Image.new("RGBA", (4, 2), "red").save(self.root/"assets/One/Res/red.png")
        self.component("One", "view", '<loader url="ui://pack0001art" size="8,8" fill="scale" vAlign="middle"/>')
        _, image = self.render()
        self.assertEqual(image.getpixel((1, 2)), (255, 0, 0, 255))
        self.assertEqual(image.getpixel((1, 1)), (0, 0, 0, 0))
        self.assertEqual(image.getpixel((1, 6)), (0, 0, 0, 0))

    def test_loader_cycle_and_pixel_limit_are_hard_failures_before_outputs(self):
        self.component("One", "view", '<loader url="ui://pack0001view" size="8,8"/>')
        with self.assertRaisesRegex(LedgerError, "cycle"):
            self.render()
        self.component("One", "view", '<loader url="ui://pack0001art" size="8,8" fill="scaleFree"/>')
        with patch("agent.ui_preview.MAX_RENDER_PIXELS", 100), self.assertRaisesRegex(LedgerError, "pixel budget"):
            self.render()
        self.assertFalse(self.output.exists())

    def test_unimplemented_image_and_list_effects_are_recorded_as_gaps(self):
        self.component("One", "child", '<image src="art"/>', size="4,4")
        self.component("One", "view", '<image src="art" color="#00ff00" fillMethod="Radial360"/>'
                       '<list size="8,8" defaultItem="ui://pack0001child"><item controllers="state,1"/></list>')
        report, _ = self.render()
        self.assertEqual(report["status"], "gap")
        self.assertIn("image tint is unsupported", report["gaps"])
        self.assertIn("list item/controller selection is unsupported", report["gaps"])

    def test_unknown_controller_selection_never_looks_like_the_requested_state(self):
        with self.assertRaisesRegex(LedgerError, "names no rendered controller"):
            self.render(controllers={"typo": "1"})
        self.assertFalse(self.output.exists())
        self.component("One", "view", '', prefix='<controller name="state" pages="a,A,b,B" selected="-1"/>')
        with self.assertRaisesRegex(LedgerError, "default controller"):
            self.render()

    def test_missing_lfs_image_is_a_visible_gap_with_no_success_status(self):
        (self.root/"assets/One/Res/red.png").write_bytes(
            b"version https://git-lfs.github.com/spec/v1\noid sha256:"+b"0"*64+b"\nsize 9\n")
        report, image = self.render()
        self.assertEqual(report["status"], "gap")
        self.assertTrue(any("unhydrated" in value for value in report["gaps"]))
        self.assertEqual(image.getpixel((1, 1)), (255, 0, 0, 255))

    def test_component_cycle_fails_before_creating_outputs(self):
        self.component("One", "view", '<component src="view" size="8,8"/>')
        with self.assertRaisesRegex(LedgerError, "cycle"):
            self.render()
        self.assertFalse(self.output.exists())

    def test_output_inside_source_existing_output_and_linked_roots_are_refused(self):
        renderer = Renderer(self.root)
        self.output.mkdir()
        for output in (self.root/"output", self.output, self.root.parent):
            with self.subTest(output=output.name), self.assertRaises(LedgerError):
                renderer.render("One", "view", output)
        link = self.private/"linked source"
        link.symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(LedgerError, "links"):
            Renderer(link)
        self.assertFalse((self.root/"output").exists())

    def test_duplicate_identities_entity_xml_and_resource_escape_are_refused(self):
        second = self.package("Two", "pack0001")
        with self.assertRaisesRegex(LedgerError, "duplicate package"):
            Renderer(self.root)
        (second/"package.xml").unlink()
        manifest = self.root/"assets/One/package.xml"
        original = manifest.read_text(encoding="utf-8")
        manifest.write_text('<!DOCTYPE data [<!ENTITY boom "data">]>'+original, encoding="utf-8")
        with self.assertRaisesRegex(LedgerError, "entities"):
            Renderer(self.root)
        manifest.write_text(original.replace('path="/Res/"', 'path="/../escape/"'), encoding="utf-8")
        with self.assertRaisesRegex(LedgerError, "path is unsafe"):
            self.render()
        self.assertFalse(self.output.exists())

    def test_real_manifest_folder_metadata_is_not_a_component_resource(self):
        manifest = self.root/"assets/One/package.xml"
        original = manifest.read_text(encoding="utf-8")
        manifest.write_text(original.replace('<resources>', '<resources><folder id="/Res/" atlas="alone_npot"/>'),
                            encoding="utf-8")
        report, image = self.render()
        self.assertEqual(report["status"], "rendered")
        self.assertEqual(image.getpixel((1, 1)), (255, 0, 0, 255))
        for value in ('<folder id="/../escape/"/>', '<folder id="/Res/"/><folder id="/Res/"/>'):
            manifest.write_text(original.replace('<resources>', '<resources>'+value), encoding="utf-8")
            with self.subTest(value=value), self.assertRaisesRegex(LedgerError, "folder metadata"):
                Renderer(self.root)

    def test_pixel_and_node_budgets_fail_without_partial_outputs(self):
        with patch("agent.ui_preview.MAX_RENDER_PIXELS", 10), self.assertRaisesRegex(LedgerError, "pixel budget"):
            self.render()
        self.component("One", "view", '<graph size="2,2"/><graph size="2,2"/>')
        with patch("agent.ui_preview.MAX_NODES", 1), self.assertRaisesRegex(LedgerError, "node budget"):
            self.render()
        self.assertFalse(self.output.exists())

    def test_unknown_effect_and_missing_cjk_font_are_explicit_gaps(self):
        self.component("One", "view", '<text text="中文" size="8,8"/><movieclip size="2,2"/>')
        report, _ = self.render()
        self.assertEqual(report["status"], "gap")
        self.assertIn("CJK/native font unavailable", report["gaps"])
        self.assertIn("unsupported display node: movieclip", report["gaps"])

    def test_source_change_during_rendering_refuses_before_outputs(self):
        renderer = Renderer(self.root)
        original = renderer.component
        def changed(*args, **kwargs):
            result = original(*args, **kwargs)
            self.component("One", "view", '<graph size="8,8"/>')
            return result
        renderer.component = changed
        with self.assertRaisesRegex(LedgerError, "changed during rendering"):
            renderer.render("One", "view", self.output)
        self.assertFalse(self.output.exists())
