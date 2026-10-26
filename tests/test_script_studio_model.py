"""Pure-data migration regressions; no GUI, hooks, Maa or game operations."""

from copy import deepcopy
import importlib.util
from pathlib import Path
import unittest

MODEL_PATH = Path(__file__).resolve().parents[1] / "agent" / "script_studio" / "model.py"
spec = importlib.util.spec_from_file_location("script_studio_model_under_test", MODEL_PATH)
model = importlib.util.module_from_spec(spec)
spec.loader.exec_module(model)
RecordingDocument = model.RecordingDocument


def doc(actions=None, duration=1000, **extra):
    raw = dict(version=1, name="测试", base=[1147,720],
        duration_ms=duration, actions=actions if actions is not None else [
            dict(t=10,op="keydown",key=74), dict(t=100,op="keyup",key=74),
            dict(t=90,op="keydown",key=74), dict(t=100,op="keyup",key=74)])
    raw.update(extra)
    return RecordingDocument.from_dict(raw)


class LegacyFormatTests(unittest.TestCase):
    def test_all_atomic_legacy_actions_and_extensions_roundtrip_exactly(self):
        raw = dict(version=1,name="旧格式",base=[1147,720], recorded_at="before",
                   custom=dict(nested=[1,2]), actions=[
            dict(t=12,op="key",key=32,custom={"keep":True}),
            dict(t=0,op="click",x=20,y=30), dict(t=10,op="text",text="中文\nhello"),
            dict(t=12,op="scroll",dx=-1,dy=2), dict(t=4,op="move",x=25,y=35),
            dict(t=8,op="down",x=30,y=40), dict(t=5,op="move",x=35,y=45),
            dict(t=5,op="up"),dict(t=1,op="keydown",key=74),dict(t=4,op="keyup",key=74)])
        recording = RecordingDocument.from_dict(raw)
        self.assertEqual(recording.to_dict(),raw)
        self.assertEqual(recording.diagnostics,[])
        self.assertEqual([o["kind"] for o in recording.snapshot()["operations"]],
                         ["key","click","text","scroll","move","mouse_hold","key_hold"])

    def test_fractional_and_missing_zero_gaps_roundtrip(self):
        raw=dict(name="fraction",actions=[dict(op="text",text=""),
            dict(t=.1,op="key",key=74),dict(t=.2,op="key",key=75)])
        self.assertEqual(RecordingDocument.from_dict(raw).to_dict(),raw)

    def test_snapshot_uses_absolute_times_and_stable_ids(self):
        recording = doc()
        snapshot = recording.snapshot()
        self.assertEqual([e["at_ms"] for e in snapshot["events"]],[10,110,200,300])
        self.assertEqual(snapshot["operations"][0]["duration_ms"],100)
        again = RecordingDocument.from_dict(recording.to_dict()).snapshot()
        self.assertEqual([e["id"] for e in again["events"]],[e["id"] for e in snapshot["events"]])
        snapshot["events"][0]["key"] = 90
        self.assertEqual(recording.snapshot()["events"][0]["key"],74)

    def test_incomplete_pairs_load_with_diagnostics_and_explicit_repair(self):
        recording=doc([dict(t=10,op="keydown",key=74),dict(t=20,op="down",x=10,y=20)])
        self.assertEqual(len(recording.diagnostics),2)
        self.assertEqual(len(recording.to_dict(strict=False)["actions"]),2)
        with self.assertRaisesRegex(ValueError,"修复"): recording.to_dict()
        repaired=recording.repair_releases()
        self.assertEqual(repaired.diagnostics,[])
        self.assertEqual([a["op"] for a in repaired.to_dict()["actions"]],
                         ["keydown","down","keyup","up"])
        self.assertEqual(len(recording.snapshot()["events"]),2)

    def test_orphan_release_is_not_silently_repaired_but_can_be_deleted(self):
        recording=doc([dict(t=10,op="keyup",key=74),dict(t=10,op="key",key=75)])
        with self.assertRaises(ValueError): recording.repair_releases()
        repaired=recording.delete(recording.snapshot()["operations"][0]["id"])
        self.assertEqual(repaired.to_dict()["actions"],[dict(t=20,op="key",key=75)])

    def test_unknown_operation_is_visible_and_preserved_for_backup(self):
        recording=doc([dict(t=0,op="future",payload=[1,2])])
        self.assertTrue(recording.diagnostics)
        self.assertEqual(recording.to_dict(strict=False)["actions"][0]["payload"],[1,2])
        with self.assertRaises(ValueError): recording.to_dict()

    def test_invalid_values_and_limits_are_rejected(self):
        for value in (-1,True,float("nan"),float("inf"),model.MAX_DURATION_MS+1,10**500):
            with self.subTest(value=value),self.assertRaises(ValueError):
                doc([dict(t=value,op="key",key=74)])
        with self.assertRaises(ValueError): doc([],duration=model.MAX_DURATION_MS+1)
        with self.assertRaises(ValueError): doc([],base=[0,720])
        with self.assertRaises(ValueError): doc([dict(t=0,op="key",key=74)]*(model.MAX_EVENTS+1))
        with self.assertRaises(ValueError): doc([dict(t=0,op="text",text="x"*model.MAX_FILE_BYTES)])

    def test_bad_action_parameters_are_diagnostics_not_data_loss(self):
        for action in (dict(t=0,op="key",key=True),dict(t=0,op="click",x=-1,y=0),
                       dict(t=0,op="scroll",dx=1.2),dict(t=0,op="text",text=5)):
            with self.subTest(action=action):
                recording=doc([action])
                self.assertTrue(recording.diagnostics)
                self.assertEqual(recording.to_dict(strict=False)["actions"],[action])


class EditingTests(unittest.TestCase):
    def test_edit_release_id_changes_only_its_pair_and_preserves_original(self):
        original=doc(); snapshot=original.snapshot()
        release_id=snapshot["events"][3]["id"]
        edited=original.edit(release_id,dict(key=75,start_ms=400,duration_ms=120))
        self.assertEqual([e["key"] for e in edited.snapshot()["events"]],[74,74,75,75])
        self.assertEqual([e["at_ms"] for e in edited.snapshot()["events"]],[10,110,400,520])
        self.assertEqual(edited.snapshot()["events"][3]["id"],release_id)
        self.assertEqual(original.snapshot(),snapshot)
        self.assertEqual(edited.duration_ms,1000)

    def test_key_overlap_rejected_and_adjacent_boundaries_allowed(self):
        original=doc(); operations=original.snapshot()["operations"]
        with self.assertRaisesRegex(ValueError,"冲突"):
            original.edit(operations[1]["id"],dict(start_ms=50,duration_ms=200))
        for start,duration in ((110,100),(300,100),(10,0)):
            with self.subTest(start=start):
                edited=original.edit(operations[1]["id"],dict(start_ms=start,duration_ms=duration))
                self.assertEqual(edited.diagnostics,[])

    def test_delete_keeps_other_times_and_trailing_duration(self):
        original=doc(); operations=original.snapshot()["operations"]
        edited=original.delete(operations[0]["id"])
        self.assertEqual([e["at_ms"] for e in edited.snapshot()["events"]],[200,300])
        self.assertEqual([a["t"] for a in edited.to_dict()["actions"]],[200,100])
        self.assertEqual(edited.duration_ms,1000)

    def test_insert_pair_extend_duration_and_preserve_extra_payload(self):
        original=doc([])
        edited=original.insert(dict(kind="mouse_hold",start_ms=1100,duration_ms=50,
                                   x=10,y=20,payload={"custom":"keep"}))
        self.assertEqual(edited.duration_ms,1150)
        self.assertEqual([a["op"] for a in edited.to_dict()["actions"]],["down","up"])
        self.assertEqual(edited.to_dict()["actions"][0]["custom"],"keep")
        self.assertEqual(original.snapshot()["events"],[])

    def test_insert_key_and_zero_length_mouse_pairs(self):
        recording=doc([]).insert(dict(kind="key_hold",key=32,start_ms=100,duration_ms=50))
        recording=recording.insert(dict(kind="mouse_hold",x=10,y=20,start_ms=100,duration_ms=0))
        self.assertEqual(recording.diagnostics,[])
        self.assertEqual(len(recording.snapshot()["operations"]),2)

    def test_atomic_text_scroll_and_click_remain_atomic(self):
        recording=doc([dict(t=10,op="text",text="before",extra=[1]),
                       dict(t=10,op="scroll",dx=1,dy=-2),dict(t=10,op="click",x=10,y=20)])
        operations=recording.snapshot()["operations"]
        recording=recording.edit(operations[0]["id"],dict(text="after",start_ms=15))
        recording=recording.edit(operations[1]["id"],dict(dx=-2,dy=3))
        self.assertEqual(recording.to_dict()["actions"][0],dict(t=15,op="text",text="after",extra=[1]))
        self.assertEqual(recording.to_dict()["actions"][1],dict(t=5,op="scroll",dx=-2,dy=3))
        with self.assertRaises(ValueError): recording.edit(operations[0]["id"],dict(duration_ms=20))

    def test_drag_translation_and_retime_preserve_other_events(self):
        recording=doc([dict(t=100,op="down",x=10,y=20),dict(t=50,op="move",x=20,y=30),
                       dict(t=50,op="up"),dict(t=200,op="key",key=74)])
        operation=recording.snapshot()["operations"][0]
        edited=recording.edit(operation["id"],dict(start_ms=200,duration_ms=200,x=20,y=40))
        events=edited.snapshot()["events"]
        self.assertEqual([e["at_ms"] for e in events],[200,300,400,400])
        self.assertEqual([(e["x"],e["y"]) for e in events[:2]],[(20,40),(30,50)])
        self.assertEqual(events[-1]["key"],74)
        self.assertEqual(edited.delete(operation["id"]).snapshot()["events"],[events[-1]])
        with self.assertRaises(ValueError): recording.edit(operation["id"],dict(x=1146,y=20))

    def test_compressed_drag_keeps_down_path_up_order(self):
        recording=doc([dict(t=100,op="down",x=10,y=20),dict(t=1,op="move",x=11,y=21),
            dict(t=98,op="move",x=12,y=22),dict(t=1,op="up")])
        operation=recording.snapshot()["operations"][0]
        for duration in (0,1):
            edited=recording.edit(operation["id"],dict(duration_ms=duration))
            self.assertEqual([e["op"] for e in edited.snapshot()["events"]],["down","move","move","up"])
            self.assertEqual(edited.diagnostics,[])

    def test_right_middle_pairs_and_shared_paths_are_not_ambiguous_edits(self):
        recording=doc([dict(t=100,op="down",button="right",x=10,y=20),
            dict(t=10,op="down",button="middle",x=10,y=20),dict(t=10,op="move",x=20,y=30),
            dict(t=10,op="up",button="right"),dict(t=10,op="up",button="middle")])
        self.assertEqual(recording.diagnostics,[])
        operations=recording.snapshot()["operations"]
        self.assertEqual({o["button"] for o in operations},{"right","middle"})
        self.assertTrue(all(o["shared_path"] for o in operations))
        for operation in operations:
            with self.assertRaisesRegex(ValueError,"共用"): recording.edit(operation["id"],dict(start_ms=200))
            with self.assertRaisesRegex(ValueError,"共用"): recording.delete(operation["id"])

    def test_retiming_cannot_absorb_another_buttons_existing_path(self):
        recording=doc([dict(t=100,op="down",x=10,y=20),dict(t=50,op="move",x=20,y=30),
            dict(t=50,op="up"),dict(t=100,op="down",button="right",x=10,y=20),
            dict(t=50,op="move",x=20,y=30),dict(t=50,op="up",button="right")])
        operation=recording.snapshot()["operations"][0]
        with self.assertRaisesRegex(ValueError,"共用"):
            recording.edit(operation["id"],dict(start_ms=300,duration_ms=100))

    def test_invalid_payloads_remain_inspectable_and_explicitly_rejected(self):
        for payload in (dict(op="down",x=1,y=2,button=[]),dict(op="keydown",key=[])):
            with self.subTest(payload=payload):
                recording=doc([dict(payload,t=0)])
                self.assertTrue(recording.snapshot()["diagnostics"])
                with self.assertRaises(ValueError): recording.repair_releases()
        recording=doc([dict(t=0,op="click",x="bad",y=2)])
        with self.assertRaises(ValueError):
            recording.edit(recording.snapshot()["operations"][0]["id"],dict(x=2))

    def test_marker_edit_delete_preserves_editor_extensions(self):
        original=doc(editor={"custom":{"keep":1}}).add_marker(123,"阶段")
        marker=original.snapshot()["markers"][0]
        edited=original.edit(marker["id"],dict(at_ms=150,label="完成"))
        self.assertEqual(edited.snapshot()["markers"],[dict(marker,at_ms=150,label="完成")])
        self.assertEqual(edited.to_dict()["editor"]["custom"],{"keep":1})
        self.assertEqual(edited.delete(marker["id"]).snapshot()["markers"],[])
        reloaded=RecordingDocument.from_dict(edited.to_dict())
        self.assertEqual(reloaded.snapshot(),edited.snapshot())

    def test_split_gap_reconstructs_exact_times_and_extensions(self):
        recording=doc(custom={"keep":True}).add_marker(500,"后半")
        before,after=recording.split(150)
        self.assertEqual((before.duration_ms,after.duration_ms),(150,850))
        reconstructed=before.snapshot()["events"]+[
            dict(e,at_ms=e["at_ms"]+150) for e in after.snapshot()["events"]]
        self.assertEqual(reconstructed,recording.snapshot()["events"])
        self.assertEqual(after.snapshot()["markers"][0]["at_ms"],350)
        self.assertEqual(before.to_dict()["custom"],{"keep":True})
        self.assertEqual(after.to_dict()["actions"][0]["t"],50)

    def test_split_shared_timestamp_can_end_after_release_before_press(self):
        recording=doc([dict(t=0,op="keydown",key=74),dict(t=100,op="keyup",key=74),
            dict(t=0,op="keydown",key=75),dict(t=100,op="keyup",key=75)])
        before,after=recording.split(100)
        self.assertEqual([e["op"] for e in before.snapshot()["events"]],["keydown","keyup"])
        self.assertEqual(after.to_dict()["actions"][0]["t"],0)

    def test_unsafe_or_empty_split_is_rejected(self):
        recording=doc()
        for at in (-1,0,50,250,400,1000,1001):
            with self.subTest(at=at),self.assertRaises(ValueError): recording.split(at)


class SourceImportTests(unittest.TestCase):
    def source(self):
        return dict(schema="zaomeng.macro",version=1,name="源录制",id="source-id",notes="保留备注",
                    window=dict(width=940,height=590,title="test",class_name="AIR"),duration_ms=500,
                    events=[dict(t_ms=10,kind="key_down",data=dict(vk=32,scan=57,extended=False)),
                            dict(t_ms=100,kind="marker",data=dict(label="暂停")),
                            dict(t_ms=200,kind="key_up",data=dict(vk=32,scan=57,extended=False)),
                            dict(t_ms=300,kind="mouse_down",data=dict(button="right",x=470,y=295)),
                            dict(t_ms=400,kind="mouse_up",data=dict(button="right",x=470,y=295)),
                            dict(t_ms=450,kind="wheel",data=dict(x=470,y=295,delta=-240,axis="vertical"))])

    def test_import_pixels_times_markers_notes_keys_and_wheel_units(self):
        source=self.source(); original=deepcopy(source)
        recording=RecordingDocument.from_source_macro(source)
        raw=recording.to_dict(); snapshot=recording.snapshot()
        self.assertEqual(source,original)
        self.assertEqual(raw["base"],[1147,720])
        self.assertEqual([a["t"] for a in raw["actions"]],[10,190,100,100,50])
        self.assertEqual(raw["actions"][2]["x"],574)
        self.assertEqual(raw["actions"][2]["y"],360)
        self.assertEqual(raw["actions"][-1]["dy"],-2)
        self.assertEqual(raw["notes"],"保留备注")
        self.assertEqual(snapshot["markers"][0]["at_ms"],100)
        first_id=snapshot["events"][0]["id"]
        self.assertEqual(raw["editor"]["source_events"][first_id]["scan"],57)
        self.assertEqual(RecordingDocument.from_dict(raw).snapshot(),snapshot)

    def test_high_precision_wheel_is_explicitly_rejected(self):
        source=self.source(); source["events"][-1]["data"]["delta"]=60
        with self.assertRaisesRegex(ValueError,"120"): RecordingDocument.from_source_macro(source)

    def test_horizontal_wheel_and_invalid_source(self):
        source=self.source(); source["events"][-1]["data"].update(delta=120,axis="horizontal")
        self.assertEqual(RecordingDocument.from_source_macro(source).to_dict()["actions"][-1]["dx"],1)
        source["events"][1]["t_ms"]=201
        with self.assertRaisesRegex(ValueError,"排序"): RecordingDocument.from_source_macro(source)

    def test_changed_imported_key_does_not_keep_a_stale_scan_code(self):
        recording=RecordingDocument.from_source_macro(self.source())
        operation=recording.snapshot()["operations"][0]
        edited=recording.edit(operation["id"],dict(key=74))
        for event_id in operation["event_ids"]:
            metadata=edited.to_dict()["editor"]["source_events"][event_id]
            self.assertEqual((metadata["vk"],metadata["scan"],metadata["extended"]),(74,0,False))

    def test_bad_source_version_and_coordinates_are_rejected(self):
        source=self.source(); source["version"]=True
        with self.assertRaises(ValueError): RecordingDocument.from_source_macro(source)
        source=self.source(); source["events"][3]["data"]["x"]=940
        with self.assertRaises(ValueError): RecordingDocument.from_source_macro(source)


if __name__ == "__main__":
    unittest.main()
