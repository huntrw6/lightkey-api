import importlib.util, os, sys, tempfile, unittest

spec = importlib.util.spec_from_file_location("lightkey_api", os.path.join(os.path.dirname(__file__), "..", "lightkey_api.py"))
lk = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lk)


def reset():
    lk.CFG.clear(); lk.CFG.update(lk.DEFAULTS)
    lk.S.update(online=True, active=None, seq=0, changed_at=0, read_at=0, read_started_at=0, read_ms=0, error=None, reads=0)
    lk.LOG.clear(); lk.DMXLOG.clear(); lk.prev_sample[0] = 0; lk.streak[0] = 0


class PublishTests(unittest.TestCase):
    def setUp(self):
        reset()

    def test_first_read_is_initial(self):
        lk.publish(active=["A", "B"], read_started_at=1000, read_ms=700)
        ev = lk.LOG[-1]
        self.assertTrue(ev["initial"]); self.assertEqual(ev["added"], ["A", "B"]); self.assertEqual(ev["seq"], 1)

    def test_no_event_when_unchanged(self):
        lk.publish(active=["A"], read_started_at=1000, read_ms=700)
        lk.publish(active=["A"], read_started_at=2000, read_ms=700)
        self.assertEqual(len(lk.LOG), 1); self.assertEqual(lk.S["seq"], 1)

    def test_added_removed_and_dmx_timestamp(self):
        lk.publish(active=["A"], read_started_at=1000, read_ms=700)
        lk.DMXLOG.append((2100, 1)); lk.DMXLOG.append((2125, 1))
        lk.publish(active=["B"], read_started_at=2000, read_ms=700)      # sample = 2000 + 425
        ev = lk.LOG[-1]
        self.assertEqual(ev["added"], ["B"]); self.assertEqual(ev["removed"], ["A"])
        self.assertEqual(ev["effective_src"], "dmx"); self.assertEqual(ev["effective_ms"], 2100)

    def test_falls_back_to_sample_time_without_dmx(self):
        lk.publish(active=["A"], read_started_at=1000, read_ms=700)
        lk.publish(active=[], read_started_at=3000, read_ms=700)
        ev = lk.LOG[-1]
        self.assertEqual(ev["effective_src"], "sample"); self.assertEqual(ev["effective_ms"], 3425)

    def test_dmx_before_previous_sample_is_ignored(self):
        lk.publish(active=["A"], read_started_at=1000, read_ms=700)       # prev sample = 1425
        lk.DMXLOG.append((1300, 1))                                       # already accounted for
        lk.publish(active=["B"], read_started_at=2000, read_ms=700)
        self.assertEqual(lk.LOG[-1]["effective_src"], "sample")

    def test_offline_event(self):
        lk.publish(active=["A"], read_started_at=1000, read_ms=700)
        lk.publish(online=False, active=None)
        ev = lk.LOG[-1]
        self.assertFalse(ev["online"]); self.assertNotIn("added", ev)

    def test_error_does_not_create_event(self):
        lk.publish(active=["A"], read_started_at=1000, read_ms=700)
        lk.publish(error="boom")
        self.assertEqual(len(lk.LOG), 1); self.assertEqual(lk.S["error"], "boom")


class ShortcutTests(unittest.TestCase):
    def test_plist_shape(self):
        p = lk.build_shortcut_plist()
        ids = [a["WFWorkflowActionIdentifier"] for a in p["WFWorkflowActions"]]
        self.assertEqual(ids, ["de.monospc.Lightkey.GetActiveCuesIntent", "is.workflow.actions.output"])
        self.assertEqual(p["WFWorkflowActions"][0]["WFWorkflowActionParameters"]["IntentAppIdentifier"], "de.monospc.Lightkey")


class ConfigTests(unittest.TestCase):
    def test_load_config_overrides(self):
        d = tempfile.mkdtemp(); p = os.path.join(d, "c.json")
        open(p, "w").write('{"port": 9999, "allow_interfaces": ["en5"]}')
        lk.load_config(p)
        self.assertEqual(lk.CFG["port"], 9999); self.assertEqual(lk.CFG["shortcut"], lk.DEFAULTS["shortcut"])
        lk.load_config(os.path.join(d, "missing.json"))
        self.assertEqual(lk.CFG["port"], 8770)


if __name__ == "__main__":
    unittest.main()
