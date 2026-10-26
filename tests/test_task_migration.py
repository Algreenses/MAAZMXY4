"""Private migration planning/transactions, without Maa, windows or user writes."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


PATH = Path(__file__).resolve().parents[1] / "tools/migrate_zaomeng_tasks.py"
SPEC = importlib.util.spec_from_file_location("task_migration_tool_under_test", PATH)
migration = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(migration)
TASK = "11111111-1111-4111-8111-111111111111"
MACRO = "22222222-2222-4222-8222-222222222222"


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(migration._encode(value))


def snapshot(root):
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def node(recognition=None, action=None, following=()):
    return dict(recognition=recognition or {"type": "DirectHit", "param": {}},
                action=action or {"type": "DoNothing", "param": {}}, next=list(following), on_error=[])


class FakeScriptLibrary:
    def __init__(self, root):
        self.root = root

    def list_scripts(self):
        return [SimpleNamespace(id=json.loads(path.read_text(encoding="utf-8"))["id"])
                for path in self.root.glob("*.json")]


class FakeAssistantLibrary:
    """The real source compiler contract; deliberately modifies its own temp profile."""
    def __init__(self, project_root, data_dir):
        self.root = data_dir / "assistant"
        self.profile = json.loads((self.root / "profile.json").read_text(encoding="utf-8"))
        self.profile["compiler_touched"] = True
        write(self.root / "profile.json", self.profile)

    def load_requests(self):
        return json.loads((self.root / "queue.json").read_text(encoding="utf-8"))

    def prepare(self, request, scripts, width, height, **options):
        assert request["macro_id"] in scripts
        raw = json.loads((self.root.parent / "scripts" / (request["macro_id"] + ".json")).read_text(encoding="utf-8"))
        pipeline = {
            "fight": node(action={"type": "Custom", "param": {"custom_action": "ZaomengRecordedMacro", "custom_action_param": {"macro": MACRO}}}, following=["done"]),
            "done": node(), "fail": node(),
            "read_a": node({"type": "TemplateMatch", "param": {"template": ["keep.png"]}}),
            "read_b": node(), "read_z": node({"type": "TemplateMatch", "param": {"template": ["unused.png"]}}),
            "go": node(), "unused": node(),
        }
        navigation = dict(version=1, targets=["b"], max_steps=10, max_seconds=60, settle_ms=0, locate_attempts=1,
                          nodes=[dict(id="a", label="a", recognition=["read_a"]), dict(id="b", label="b", recognition=["read_b"]),
                                 dict(id="z", label="z", recognition=["read_z"])],
                          edges=[dict(id="ab", source="a", target="b", label="go", entry="go", cost=1, timeout_ms=1000, max_attempts=1)])
        return SimpleNamespace(flow=SimpleNamespace(pipeline=pipeline, entry="fight", max_seconds=600, macros={MACRO: raw}),
                               root=self.root / "resource", label="示例悬赏", success_node="done", failure_nodes={"fail": "停止"},
                               navigation=navigation, warnings=["仍需实际客户端确认"])


class TaskMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source, self.target = self.root / "source", self.root / "target"
        self.source.mkdir(); self.target.mkdir()
        raw = dict(schema="zaomeng.macro", version=1, id=MACRO, name="原像素录制", duration_ms=2000,
                   window=dict(width=1175, height=737), events=[
                       dict(t_ms=100, kind="key_down", data=dict(vk=68)),
                       dict(t_ms=200, kind="mouse_down", data=dict(x=1000, y=700, button="left")),
                       dict(t_ms=500, kind="mouse_up", data=dict(x=1100, y=710, button="left")),
                       dict(t_ms=600, kind="key_up", data=dict(vk=68)),
                       dict(t_ms=700, kind="marker", data=dict(label="结束"))])
        write(self.source / f"data/scripts/{MACRO}.json", raw)
        write(self.source / "data/assistant/queue.json", [dict(id=TASK, task_id="bounty_recorded_return", macro_id=MACRO, iterations=999, enabled=True)])
        write(self.source / "data/assistant/profile.json", dict(width=1175, height=737, detectors={}))
        write(self.source / "data/assistant/recovery.json", dict(enabled=True, save_slot=1))
        for name in ("keep.png", "unused.png"):
            path = self.source / "data/assistant/resource/image" / name
            path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(name.encode())
        for name in ("det.onnx", "rec.onnx", "keys.txt", "SOURCE.json"):
            path = self.source / "data/models/ocr" / name
            path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(("source-" + name).encode())
        write(self.target / "interface.json", dict(task=[dict(name="旧任务", entry="old", default_check=True)],
              controller=[dict(name="旧窗口", type="Win32", win32=dict(screencap="FramePool", mouse="SendMessage", keyboard="SendMessage"))],
              resource=[dict(name="默认", path=["./resource/base"])], option={}))
        write(self.target / "appsettings.json", {"Instances.List": "default", "Instances.Order": "default", "Instances.LastActive": "default"})
        write(self.target / "config/instances/default.json", {"InstanceName": "原配置", "user_setting": 17})
        self.original_instance = (self.target / "config/instances/default.json").read_bytes()
        current_hash = migration._sha((self.source / f"data/scripts/{MACRO}.json").read_bytes())
        self.result = {"recordings": [dict(source_id=MACRO, source_sha256="old", file="旧版本.json"),
                                     dict(source_id=MACRO, source_sha256=current_hash, file="已编辑720.json", status="target_modified")]}
        flows = SimpleNamespace(asset_path=lambda root, name: root / "image" / name,
            recognition_templates=lambda recognition: recognition.get("param", {}).get("template", []))
        modules = ("_fake_task_source", SimpleNamespace(AssistantLibrary=FakeAssistantLibrary),
                   SimpleNamespace(ScriptLibrary=FakeScriptLibrary), flows)
        self.source_modules = patch.object(migration, "_source_modules", return_value=modules)
        self.source_modules.start(); self.addCleanup(self.source_modules.stop)

    def migrate(self, dry_run=False):
        return migration.migrate_tasks(self.source, self.target, self.result, dry_run=dry_run)

    def test_dry_run_compiles_fresh_temp_data_without_source_or_target_writes(self):
        before_source, before_target = snapshot(self.source), snapshot(self.target)
        result = self.migrate(dry_run=True)
        self.assertFalse(result["applied"])
        self.assertEqual(snapshot(self.source), before_source)
        self.assertEqual(snapshot(self.target), before_target)
        task = result["tasks"][0]
        self.assertEqual(task["source_iterations"], 999)
        self.assertTrue(task["source_enabled"])
        self.assertTrue(task["library_target_modified"])
        self.assertEqual(task["library_recording"]["file"], "已编辑720.json")
        self.assertEqual(len(task["bundle"]["navigation"]["nodes"]), 2)
        self.assertNotIn("read_z", task["bundle"]["pipeline"])

    def test_apply_registers_raw_resource_and_disabled_single_round_task(self):
        result = self.migrate()
        self.assertTrue(result["applied"])
        interface = migration._read_json(self.target / "interface.json")
        raw = next(c for c in interface["controller"] if c["name"] == migration.CONTROLLER_NAME)
        self.assertTrue(raw["display_raw"])
        self.assertEqual(raw["win32"]["mouse"], "Seize")
        self.assertEqual(interface["controller"][0]["task"], ["旧任务"])
        self.assertEqual(interface["controller"][0]["win32"]["mouse"], "SendMessage")
        instance = migration._read_json(self.target / migration.INSTANCE_PATH)
        self.assertFalse(instance["ContinueRunningWhenError"])
        self.assertFalse(instance["IncludeInGlobalStart"])
        self.assertFalse(instance["TaskItems"][0]["default_check"])
        self.assertEqual(instance["TaskItems"][0]["repeat_count"], 999)
        self.assertEqual(instance["Resource"], migration.RESOURCE_NAME)
        self.assertEqual((self.target / "config/instances/default.json").read_bytes(), self.original_instance)
        self.assertEqual(migration._read_json(self.target / "appsettings.json")["Instances.LastActive"], "default")
        bundle = migration._read_json(self.target / result["tasks"][0]["bundle_path"])
        self.assertEqual(bundle["iterations"], 1)
        self.assertEqual(bundle["recovery"], dict(enabled=True, save_slot=1))
        raw_macro = migration._read_json(self.target / f"{migration.PRIVATE}/tasks/{TASK}/raw_recordings/{MACRO}.json")
        self.assertEqual(raw_macro["base"], [1175, 737])
        self.assertEqual(raw_macro["actions"][1]["x"], 1000)
        self.assertEqual(raw_macro["actions"][2]["y"], 710)
        self.assertEqual(sum(action["t"] for action in raw_macro["actions"]), 600)
        self.assertEqual(raw_macro["duration_ms"], 2000)
        self.assertEqual((self.target / f"{migration.RESOURCE}/model/ocr/rec.onnx").read_bytes(), b"source-rec.onnx")
        self.assertFalse((self.target / f"{migration.PRIVATE}/tasks/{TASK}/image/unused.png").exists())
        self.assertIn("原工程自动重启恢复尚未接入，失败停止", instance["TaskItems"][0]["description"])

    def test_repeat_migration_keeps_user_instance_edits_and_is_idempotent(self):
        self.migrate()
        path = self.target / migration.INSTANCE_PATH
        instance = migration._read_json(path)
        instance["TaskItems"][0].update(repeat_count=7, default_check=True, display_name_override="我改的名字")
        write(path, instance)
        result = self.migrate()
        self.assertEqual(result["changes"], [])
        self.assertEqual(migration._read_json(path)["TaskItems"][0]["repeat_count"], 7)
        self.assertTrue(migration._read_json(path)["TaskItems"][0]["default_check"])

    def test_modified_bundle_blocks_transaction_without_overwriting_user_file(self):
        self.migrate()
        path = self.target / f"{migration.PRIVATE}/tasks/{TASK}/bundle.json"
        bundle = migration._read_json(path); bundle["max_seconds"] = 17; write(path, bundle)
        before = snapshot(self.target)
        self.assertIn(path.relative_to(self.target).as_posix(), self.migrate(dry_run=True)["conflicts"])
        with self.assertRaises(migration.MigrationConflict): self.migrate()
        self.assertEqual(snapshot(self.target), before)

    def test_frontend_json_reformatting_does_not_create_configuration_changes(self):
        self.migrate()
        for relative in ("interface.json", migration.INSTANCE_PATH, "appsettings.json"):
            path = self.target / relative
            value = migration._read_json(path)
            reordered = dict(reversed(list(value.items())))
            path.write_bytes(("\ufeff" + json.dumps(reordered, ensure_ascii=True, indent=4) + "\n\n").encode("utf-8"))
        before = snapshot(self.target)
        result = self.migrate(dry_run=True)
        self.assertEqual(result["changes"], [])
        result = self.migrate()
        self.assertEqual(result["changes"], [])
        self.assertIsNone(result["backup"])
        self.assertEqual(snapshot(self.target), before)

    def test_stale_script_result_cannot_silently_select_historical_macro(self):
        self.result["recordings"] = self.result["recordings"][:1]
        with self.assertRaisesRegex(migration.MigrationConflict, "当前源录制版本"):
            self.migrate(dry_run=True)

    def test_source_change_during_compile_blocks_apply(self):
        original = FakeAssistantLibrary.prepare
        def changing(library, *args, **kwargs):
            result = original(library, *args, **kwargs)
            write(self.source / "data/assistant/recovery.json", dict(enabled=False))
            return result
        before = snapshot(self.target)
        with patch.object(FakeAssistantLibrary, "prepare", changing):
            with self.assertRaises(migration.MigrationConflict): self.migrate()
        self.assertEqual(snapshot(self.target), before)

    def test_config_race_keeps_concurrent_user_change(self):
        original = FakeAssistantLibrary.prepare
        def changing(library, *args, **kwargs):
            result = original(library, *args, **kwargs)
            interface = migration._read_json(self.target / "interface.json")
            interface["user_changed"] = True; write(self.target / "interface.json", interface)
            return result
        with patch.object(FakeAssistantLibrary, "prepare", changing):
            with self.assertRaises(migration.MigrationConflict): self.migrate()
        self.assertTrue(migration._read_json(self.target / "interface.json")["user_changed"])
        self.assertFalse((self.target / migration.INSTANCE_PATH).exists())

    def test_writes_back_up_changed_configuration(self):
        original = (self.target / "interface.json").read_bytes()
        result = self.migrate()
        self.assertEqual((self.target / result["backup"] / "interface.json").read_bytes(), original)

    def test_raw_nonstandard_wheel_is_rejected(self):
        raw = migration._read_json(self.source / f"data/scripts/{MACRO}.json")
        raw["events"] = [dict(t_ms=10, kind="wheel", data=dict(x=2, y=3, axis="vertical", delta=15))]
        with self.assertRaisesRegex(ValueError, "120"):
            migration.raw_recording(raw)


if __name__ == "__main__":
    unittest.main()
