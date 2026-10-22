import importlib.util, os, tempfile, unittest

spec = importlib.util.spec_from_file_location("lightkey_api", os.path.join(os.path.dirname(__file__), "..", "lightkey_api.py"))
lk = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lk)


class ConfigTests(unittest.TestCase):
    def test_load_config_overrides(self):
        d = tempfile.mkdtemp(); p = os.path.join(d, "c.json")
        with open(p, "w") as f:
            f.write('{"port": 9999, "allow_interfaces": ["en5"]}')
        lk.load_config(p)
        self.assertEqual(lk.CFG["port"], 9999); self.assertEqual(lk.CFG["allow_interfaces"], ["en5"])
        lk.load_config(os.path.join(d, "missing.json"))
        self.assertEqual(lk.CFG["port"], 8770)

    def test_core_has_no_cue_settings(self):
        for key in ("shortcut", "backstop_s", "quiet_gap_s", "busy_gap_s", "max_parallel_reads", "read_timeout_s", "log_size"):
            self.assertNotIn(key, lk.DEFAULTS)
        self.assertFalse(lk.DEFAULTS["cues"])

    def test_core_never_mentions_the_shortcut_machinery(self):
        text = open(os.path.join(os.path.dirname(__file__), "..", "lightkey_api.py")).read()
        for word in ("/usr/bin/shortcuts", "build_shortcut_plist", "read_active", "GetActiveCues"):
            self.assertNotIn(word, text)


class WithoutCuesTests(unittest.TestCase):
    """The agent must work with only lightkey_api.py: no cue module, no Shortcut."""

    def setUp(self):
        lk.CFG.clear(); lk.CFG.update(lk.DEFAULTS)
        for k in ("online", "get"):
            lk.HOOKS[k] = [] if k == "online" else {}
        lk.HOOKS["dmx"] = []

    def test_snapshot_and_health_need_no_cue_state(self):
        s = lk.snapshot()
        self.assertEqual(s["version"], lk.__version__); self.assertFalse(s["cues_enabled"])
        self.assertNotIn("active", s)

    def test_cue_routes_are_not_listed_until_the_module_registers_them(self):
        self.assertNotIn("/state", lk.INDEX["endpoints"])
        self.assertNotIn("/log?since=N", lk.INDEX["endpoints"])

    def test_cues_on_without_the_module_does_not_crash(self):
        spec2 = importlib.util.spec_from_file_location("lightkey_api2", os.path.join(os.path.dirname(__file__), "..", "lightkey_api.py"))
        other = importlib.util.module_from_spec(spec2); spec2.loader.exec_module(other)
        other.__file__ = os.path.join(tempfile.mkdtemp(), "lightkey_api.py")       # a folder with no lightkey_cues.py
        self.assertIsNone(other.load_cues())


if __name__ == "__main__":
    unittest.main()
