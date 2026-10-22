import importlib.util, os, unittest
from collections import deque

here = os.path.dirname(__file__)
spec = importlib.util.spec_from_file_location("lightkey_api", os.path.join(here, "..", "lightkey_api.py"))
lk = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lk)
spec2 = importlib.util.spec_from_file_location("lightkey_cues", os.path.join(here, "..", "lightkey_cues.py"))
cues = importlib.util.module_from_spec(spec2)
spec2.loader.exec_module(cues)


def reset():
    lk.CFG.clear(); lk.CFG.update(lk.DEFAULTS)
    lk.S.clear(); lk.S.update(online=True, dmx_seen_at=0, dmx_changed_at=0, error=None)
    lk.HOOKS["online"] = []; lk.HOOKS["dmx"] = []; lk.HOOKS["get"] = {}
    lk.INDEX["endpoints"].pop("/state", None)
    cues.attach(lk)
    cues.LOG.clear(); cues.DMXLOG.clear(); cues.prev_sample[0] = 0; lk.streak[0] = 0


class AttachTests(unittest.TestCase):
    def setUp(self):
        reset()

    def test_attach_registers_routes_hooks_and_settings(self):
        self.assertEqual(set(lk.HOOKS["get"]), {"/state", "/log", "/events"})
        self.assertEqual(len(lk.HOOKS["online"]), 1); self.assertEqual(len(lk.HOOKS["dmx"]), 1)
        self.assertIn("/state", lk.INDEX["endpoints"])
        self.assertEqual(lk.CFG["shortcut"], "Lightkey API Active Cues"); self.assertEqual(lk.CFG["log_size"], 500)
        self.assertIsNone(lk.S["active"])

    def test_a_user_setting_is_not_overwritten_by_the_cue_defaults(self):
        lk.CFG["shortcut"] = "Mine"
        cues.attach(lk)
        self.assertEqual(lk.CFG["shortcut"], "Mine")

    def test_the_dmx_hook_notes_the_frame_and_wakes_the_reader(self):
        lk.wake.clear(); before = lk.dirty_at[0]
        lk.HOOKS["dmx"][0](1234, 1)
        self.assertEqual(list(cues.DMXLOG), [(1234, 1)]); self.assertTrue(lk.wake.is_set()); self.assertGreater(lk.dirty_at[0], before)


class PublishTests(unittest.TestCase):
    def setUp(self):
        reset()

    def test_first_read_is_initial(self):
        cues.publish(active=["A", "B"], read_started_at=1000, read_ms=700)
        ev = cues.LOG[-1]
        self.assertTrue(ev["initial"]); self.assertEqual(ev["added"], ["A", "B"]); self.assertEqual(ev["seq"], 1)

    def test_no_event_when_unchanged(self):
        cues.publish(active=["A"], read_started_at=1000, read_ms=700)
        cues.publish(active=["A"], read_started_at=2000, read_ms=700)
        self.assertEqual(len(cues.LOG), 1); self.assertEqual(lk.S["seq"], 1)

    def test_added_removed_and_dmx_timestamp(self):
        cues.publish(active=["A"], read_started_at=1000, read_ms=700)
        cues.DMXLOG.append((2100, 1)); cues.DMXLOG.append((2125, 1))
        cues.publish(active=["B"], read_started_at=2000, read_ms=700)      # sample = 2000 + 425
        ev = cues.LOG[-1]
        self.assertEqual(ev["added"], ["B"]); self.assertEqual(ev["removed"], ["A"])
        self.assertEqual(ev["effective_src"], "dmx"); self.assertEqual(ev["effective_ms"], 2100)

    def test_falls_back_to_sample_time_without_dmx(self):
        cues.publish(active=["A"], read_started_at=1000, read_ms=700)
        cues.publish(active=[], read_started_at=3000, read_ms=700)
        ev = cues.LOG[-1]
        self.assertEqual(ev["effective_src"], "sample"); self.assertEqual(ev["effective_ms"], 3425)

    def test_dmx_before_previous_sample_is_ignored(self):
        cues.publish(active=["A"], read_started_at=1000, read_ms=700)       # prev sample = 1425
        cues.DMXLOG.append((1300, 1))                                       # already accounted for
        cues.publish(active=["B"], read_started_at=2000, read_ms=700)
        self.assertEqual(cues.LOG[-1]["effective_src"], "sample")

    def test_offline_event(self):
        cues.publish(active=["A"], read_started_at=1000, read_ms=700)
        lk.HOOKS["online"][0](False)
        ev = cues.LOG[-1]
        self.assertFalse(ev["online"]); self.assertNotIn("added", ev)
        self.assertIsNone(lk.S["active"])

    def test_error_does_not_create_event(self):
        cues.publish(active=["A"], read_started_at=1000, read_ms=700)
        cues.publish(error="boom")
        self.assertEqual(len(cues.LOG), 1); self.assertEqual(lk.S["error"], "boom")


class ShortcutTests(unittest.TestCase):
    def test_plist_shape(self):
        p = cues.build_shortcut_plist()
        ids = [a["WFWorkflowActionIdentifier"] for a in p["WFWorkflowActions"]]
        self.assertEqual(ids, ["de.monospc.Lightkey.GetActiveCuesIntent", "is.workflow.actions.output"])
        self.assertEqual(p["WFWorkflowActions"][0]["WFWorkflowActionParameters"]["IntentAppIdentifier"], "de.monospc.Lightkey")


class RoutesTests(unittest.TestCase):
    class Fake:
        def __init__(self): self.sent = None
        def _json(self, obj, code=200): self.sent = (code, obj)

    def setUp(self):
        reset()

    def test_state_and_log_answer_with_a_count(self):
        cues.publish(active=["A", "B"], read_started_at=1000, read_ms=700)
        h = self.Fake(); cues.get_state(h, {})
        self.assertEqual(h.sent[1]["count"], 2); self.assertEqual(h.sent[1]["active"], ["A", "B"])
        h = self.Fake(); cues.get_log(h, {"since": "0"})
        self.assertEqual(len(h.sent[1]["events"]), 1); self.assertEqual(h.sent[1]["state"]["count"], 2)
        h = self.Fake(); cues.get_log(h, {"since": "x"})
        self.assertEqual(h.sent[0], 400)


if __name__ == "__main__":
    unittest.main()
