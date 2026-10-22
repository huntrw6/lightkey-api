import importlib.util, json, os, tempfile, unittest

spec = importlib.util.spec_from_file_location("lightkey_api", os.path.join(os.path.dirname(__file__), "..", "lightkey_api.py"))
lk = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lk)


def cap(kind, channel, params=None, beams=None, name="", settings=None):
    return {"kind": kind, "channel": channel, "beams": beams, "name": name,
            "settings": settings if settings is not None else [{"lo": 0, "hi": -1, "params": params or {}}]}


RGB = {"name": "P1", "profile": "Acme Pixel", "personality": "", "universe": 1, "address": 10, "footprint": 3,
       "panRange": 0, "tiltRange": 0,
       "capabilities": [cap("ColorComponent", 0, {"componentName": "Red"}), cap("ColorComponent", 1, {"componentName": "Green"}),
                        cap("ColorComponent", 2, {"componentName": "Blue"})]}
MOVER = {"name": "MH1", "profile": "Acme Mover", "personality": "Extended", "universe": 1, "address": 100, "footprint": 8,
         "panRange": 540.0, "tiltRange": 270.0,
         "capabilities": [cap("Pan", 0), cap("PanFine", 1), cap("Tilt", 2), cap("TiltFine", 3), cap("Intensity", 4),
                          cap("ColorWheel", 5, settings=[
                              {"lo": 0, "hi": 7, "params": {"mode": 0, "color": {"Cmps": [255, 255, 255]}}},
                              {"lo": 8, "hi": 14, "params": {"mode": 0, "color": {"Cmps": [255, 0, 0]}}}]),
                          cap("Custom", 6, {"name": "Auto Program", "continuous": True}),
                          cap("ColorComponent", 7, {"componentName": "Amber"})]}
RGBW = {"name": "P25", "profile": "Acme RGBW", "personality": "", "universe": 2, "address": 0, "footprint": 5,
        "panRange": 0, "tiltRange": 0,
        "capabilities": [cap("ColorComponent", i, {"componentName": n}) for i, n in enumerate(["Red", "Green", "Blue", "White"])]
        + [cap("Intensity", 4)]}
FMAP = {"version": 1, "project": "t.lightkeyproj", "fixtures": [RGB, MOVER, RGBW]}


def frame(**slots):
    d = bytearray(512)
    for k, v in slots.items():
        d[int(k[1:])] = v
    return bytes(d)


class DecodeTests(unittest.TestCase):
    def setUp(self):
        self.fx = lk.Fixtures(FMAP)

    def test_rgb_folds_into_one_color(self):
        p = self.fx.decode(0, {1: frame(s10=255, s11=128, s12=0)})
        self.assertEqual(p["color"], [1.0, 0.502, 0.0])
        self.assertTrue(p["lit"])
        self.assertNotIn("red", p)

    def test_dark_rgb_is_not_lit(self):
        self.assertFalse(self.fx.decode(0, {1: frame()})["lit"])

    def test_pan_is_sixteen_bit_with_degrees(self):
        p = self.fx.decode(1, {1: frame(s100=128, s101=0, s102=255, s103=255)})
        self.assertAlmostEqual(p["pan"], 32768 / 65535.0, places=4)
        self.assertAlmostEqual(p["panAngle"], 0.0, delta=0.05)          # 16-bit centre = angle 0
        self.assertEqual(p["tilt"], 1.0)
        self.assertEqual(p["tiltAngle"], 135.0)                         # top of a 270 degree range

    def test_coarse_pan_centres_on_dmx_127(self):
        cfg = dict(MOVER, capabilities=[c for c in MOVER["capabilities"] if c["kind"] not in ("PanFine", "TiltFine")])
        fx = lk.Fixtures({"fixtures": [cfg]})
        self.assertEqual(fx.decode(0, {1: frame(s100=127)})["panAngle"], 0.0)
        self.assertAlmostEqual(fx.decode(0, {1: frame(s100=255)})["panAngle"], 539.0 * 0.5 + 0.0, delta=3.0)

    def test_dimmer_and_color_wheel_and_custom(self):
        p = self.fx.decode(1, {1: frame(s104=128, s105=10, s106=51)})
        self.assertAlmostEqual(p["dimmer"], 0.502, places=3)
        self.assertEqual(p["colorWheel"]["color"], [1.0, 0.0, 0.0])
        self.assertEqual(p["colorWheel"]["dmx"], 10)
        self.assertAlmostEqual(p["autoProgram"], 0.2, places=3)
        self.assertTrue(p["lit"])

    def test_rgbw_dimmer_zero_is_not_lit_even_with_color(self):
        p = self.fx.decode(2, {2: frame(s0=255, s4=0)})
        self.assertFalse(p["lit"])
        self.assertEqual(p["color"], [1.0, 0.0, 0.0])

    def test_missing_universe_returns_none(self):
        self.assertIsNone(self.fx.decode(0, {2: frame()}))

    def test_slot_index_ignores_other_universes(self):
        self.assertEqual(self.fx.affected(1, [10]), {0})
        self.assertEqual(self.fx.affected(2, [10]), set())
        self.assertEqual(self.fx.affected(1, [100, 102]), {1})

    def test_summary_lists_one_based_address_and_properties(self):
        s = {f["name"]: f for f in self.fx.summary()}
        self.assertEqual(s["P1"]["address"], 11)
        self.assertEqual(s["MH1"]["properties"], ["amber", "autoProgram", "colorWheel", "dimmer", "lit", "pan", "panAngle", "tilt", "tiltAngle"])
        self.assertEqual(s["P1"]["properties"], ["color", "lit"])


BROKEN = {"name": "X1", "profile": "Bad", "personality": "", "universe": 1, "address": 200, "footprint": 1,
          "panRange": 0, "tiltRange": 0,
          "capabilities": [cap("ColorWheel", 0, settings=[{"lo": 0, "hi": 255, "params": {"color": {"Cmps": None}}}])]}


class TrackerTests(unittest.TestCase):
    def setUp(self):
        self.tr = lk.LevelTracker(lk.Fixtures(FMAP), 100)

    def test_first_frame_baselines_without_event(self):
        self.assertFalse(self.tr.frame(1, frame(), 1000))
        self.assertEqual(self.tr.seq, 0)
        self.assertIn("P1", self.tr.state())

    def test_change_logged_with_only_changed_properties(self):
        self.tr.frame(1, frame(s10=0), 1000)
        self.assertTrue(self.tr.frame(1, frame(s10=255), 1040))
        e = self.tr.log[-1]
        self.assertEqual(e["t"], 1040)
        self.assertEqual(e["changes"]["P1"]["color"], [1.0, 0.0, 0.0])
        self.assertTrue(e["changes"]["P1"]["lit"])
        self.assertNotIn("MH1", e["changes"])

    def test_change_to_unmapped_slot_is_not_an_event(self):
        self.tr.frame(1, frame(), 1000)
        self.assertFalse(self.tr.frame(1, frame(s400=9), 1010))
        self.assertEqual(self.tr.seq, 0)

    def test_state_is_current(self):
        self.tr.frame(1, frame(), 1000)
        self.tr.frame(1, frame(s104=255), 1010)
        self.assertEqual(self.tr.state()["MH1"]["dimmer"], 1.0)

    def test_colour_wheel_without_components_does_not_crash(self):
        fx = lk.Fixtures({"fixtures": [BROKEN, RGB]})
        self.assertEqual(fx.decode(0, {1: frame()})["colorWheel"], {"dmx": 0})

    def test_one_bad_fixture_does_not_stop_the_others(self):
        class Boom(lk.Fixtures):
            def decode(self, i, levels):
                if self.fixtures[i]["name"] == "MH1":
                    raise ValueError("boom")
                return super().decode(i, levels)
        tr = lk.LevelTracker(Boom(FMAP), 10)
        tr.frame(1, frame(), 1000)
        self.assertTrue(tr.frame(1, frame(s10=255, s104=255), 1010))
        self.assertEqual(list(tr.log[-1]["changes"]), ["P1"])

    def test_two_universes_are_independent(self):
        self.tr.frame(1, frame(), 1000)
        self.tr.frame(2, frame(), 1000)
        self.tr.frame(2, frame(s4=255, s3=255), 1020)
        self.assertEqual(self.tr.log[-1]["universe"], 2)
        self.assertEqual(list(self.tr.log[-1]["changes"]), ["P25"])


class HelperTests(unittest.TestCase):
    def test_project_map_is_json_safe(self):
        import plistlib as pl
        # a minimal NSKeyedArchive: one profile (with nan, bytes) and one fixture
        uid = pl.UID
        objs = ["$null",
                {"$classname": "LXFixtureProfile"}, {"$classname": "LXDMXFixture"}, {"$classname": "NSArray"},
                {"$classname": "LXPersonality"}, {"$classname": "NSSet"}, {"$classname": "LXIntensityCapability"},
                {"$classname": "LXSetting"}, {"$classname": "NSUUID"}, {"$classname": "NSDictionary"},
                {"$class": uid(8), "NS.uuidbytes": b"0123456789abcdef"},                    # 10 profile uuid
                {"$class": uid(3), "NS.objects": [uid(12)]},                                   # 11 personalities
                {"$class": uid(4), "customName": "Basic", "footprint": 1, "capabilities": uid(13)},  # 12
                {"$class": uid(5), "NS.objects": [uid(14)]},                                   # 13 capabilities
                {"$class": uid(6), "channel": 0, "customName": "$null", "beamIndexes": "$null", "settings": uid(15)},  # 14
                {"$class": uid(3), "NS.objects": [uid(16)]},                                   # 15 settings
                {"$class": uid(7), "$0": uid(17), "params": uid(18)},                          # 16
                {"$class": uid(3), "NS.objects": []},                                          # 17 (range, ignored shape)
                {"$class": uid(9), "NS.keys": ["blob"], "NS.objects": [uid(19)]},              # 18 params
                b"\x00\x01",                                                                   # 19 bytes
                {"$class": uid(1), "name": "Dimmer", "manufacturer": "Acme", "UUID": uid(10), "personalities": uid(11),
                 "panRange": float("nan"), "tiltRange": 0.0, "tiltCenter": 0.0},                # 20
                {"$class": uid(2), "shortName": "D1", "personalityIndex": 0, "address": 0, "fixtureProfileUUID": uid(10),
                 "universe": uid(21)},                                                          # 21? fixed below
                ]
        objs[21] = {"$class": uid(2), "shortName": "D1", "personalityIndex": 0, "address": 0,
                    "fixtureProfileUUID": uid(10), "universe": uid(22)}
        objs.append({"index": 3})                                                               # 22
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "t.lightkeyproj")
            with open(path, "wb") as fh:
                pl.dump({"$archiver": "NSKeyedArchiver", "$version": 100000, "$top": {}, "$objects": objs}, fh, fmt=pl.FMT_BINARY)
            fmap = lk.parse_project(path)
        json.dumps(fmap)                                            # must not raise
        self.assertEqual(fmap["fixtures"][0]["name"], "D1")
        self.assertEqual(fmap["fixtures"][0]["universe"], 3)
        self.assertEqual(fmap["fixtures"][0]["capabilities"][0]["kind"], "Intensity")


    def test_camel(self):
        self.assertEqual(lk._camel("Auto Program Speed"), "autoProgramSpeed")
        self.assertEqual(lk._camel(""), "")

    def test_load_fixtures_picks_up_file_and_changes(self):
        lk.CFG.clear(); lk.CFG.update(lk.DEFAULTS)
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "fixtures.json")
            lk.CFG["fixtures_file"] = path
            lk.FIXTURES[0] = None; lk.TRACKER[0] = None; lk.LEVELS.clear()
            lk.load_fixtures()
            self.assertIsNone(lk.FIXTURES[0])
            lk.LEVELS[1] = frame(s10=255)
            with open(path, "w") as fh:
                json.dump(FMAP, fh)
            lk.load_fixtures()
            self.assertEqual(len(lk.FIXTURES[0][0].fixtures), 3)
            self.assertEqual(lk.TRACKER[0].state()["P1"]["color"], [1.0, 0.0, 0.0])   # levels seen before the map existed
        lk.FIXTURES[0] = None; lk.TRACKER[0] = None; lk.LEVELS.clear()


if __name__ == "__main__":
    unittest.main()
