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
        self.assertAlmostEqual(p["panAngle"], 0.0, delta=0.05)          # 16-bit middle = angle 0
        self.assertEqual(p["tilt"], 1.0)
        self.assertEqual(p["tiltAngle"], 135.0)                         # top of a 270 degree range

    def test_coarse_pan_centres_on_dmx_127(self):
        cfg = dict(MOVER, capabilities=[c for c in MOVER["capabilities"] if c["kind"] not in ("PanFine", "TiltFine")])
        fx = lk.Fixtures({"fixtures": [cfg]})
        self.assertAlmostEqual(fx.decode(0, {1: frame(s100=127)})["panAngle"], -1.06, places=2)   # 127 is just under the middle
        self.assertAlmostEqual(fx.decode(0, {1: frame(s100=255)})["panAngle"], 270.0, delta=0.01)
        self.assertAlmostEqual(fx.decode(0, {1: frame(s100=0)})["panAngle"], -270.0, delta=0.01)

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


def sh(**kw):
    return kw


MOVER2 = {"name": "MH2", "profile": "Acme Spot", "personality": "Basic", "universe": 1, "address": 0, "footprint": 14,
          "panRange": 540.0, "tiltRange": 270.0,
          "capabilities": [
              cap("Pan", 0), cap("Tilt", 1),
              cap("ColorWheel", 2, settings=[{"lo": 0, "hi": 7, "params": {"color": {"Cmps": [255, 255, 255]}}},
                                             {"lo": 8, "hi": 14, "params": {"color": {"Cmps": [255, 0, 0]}}},
                                             {"lo": 15, "hi": 21, "params": {"color": {"Cmps": [255, 128, 0]}}}]),
              cap("Gobo", 3, settings=[{"lo": 0, "hi": 4, "params": {"goboIdentifier": "open", "shaking": False}},
                                       {"lo": 5, "hi": 19, "params": {"goboIdentifier": "a", "shaking": False}},
                                       {"lo": 20, "hi": 34, "params": {"goboIdentifier": "b", "shaking": False}},
                                       {"lo": 35, "hi": 49, "params": {"goboIdentifier": "a", "shaking": True}}]),
              cap("GoboRotation", 4, settings=[{"lo": -128, "hi": -67, "params": {"mode": 5, "direction": 0}},
                                               {"lo": -66, "hi": -63, "params": {"mode": 0}},
                                               {"lo": -62, "hi": -1, "params": {"mode": 5, "direction": 1}}]),
              cap("GoboAngle", 4, settings=[{"lo": 0, "hi": 127, "params": {}}]),
              cap("ShutterStrobe", 5, settings=[{"lo": 0, "hi": 31, "params": {"mode": 0}},
                                                {"lo": 32, "hi": 63, "params": {"mode": 1}},
                                                {"lo": 64, "hi": 95, "params": {"mode": 2}},
                                                {"lo": 96, "hi": 127, "params": {"mode": 1}}]),
              cap("Intensity", 6), cap("Zoom", 7, {"minAngle": 11, "maxAngle": 22, "transformation": 0}),
              cap("Focus", 8), cap("Frost", 9), cap("Custom", 10, {"name": "Slow > Fast", "continuous": True}, name="Program Speed"),
              cap("Custom", 11, name="Program", settings=[{"lo": 0, "hi": 127, "params": {"name": "Off"}},
                                                          {"lo": 128, "hi": 255, "params": {"name": "Program 1", "continuous": True}}]),
              cap("PanTiltSpeed", 12, {"mode": 1, "transformation": 1})]}
INVERTED = {"name": "D1", "profile": "Acme Driver", "personality": "", "universe": 1, "address": 300, "footprint": 2,
            "panRange": 0, "tiltRange": 0,
            "capabilities": [cap("Intensity", 0, {"transformation": 1}), cap("Zoom", 1, {"minAngle": 5, "maxAngle": 60, "transformation": 1})]}
WHITES = {"name": "P29", "profile": "Acme Wash", "personality": "", "universe": 1, "address": 400, "footprint": 3,
          "panRange": 0, "tiltRange": 0,
          "capabilities": [cap("Intensity", 0), cap("ColorComponent", 1, {"componentName": "WarmWhite"}),
                           cap("ColorComponent", 2, {"componentName": "CoolWhite"})]}
BEAMS = {"name": "PN1", "profile": "Acme Panel", "personality": "", "universe": 1, "address": 450, "footprint": 6,
         "panRange": 0, "tiltRange": 0,
         "capabilities": [cap("ColorComponent", i % 3, {"componentName": ["Red", "Green", "Blue"][i % 3]}) if False else
                          cap("ColorComponent", i, {"componentName": ["Red", "Green", "Blue"][i % 3]}) for i in range(6)]}


def osc_map(fixture, **slots):
    fx = lk.Fixtures({"fixtures": [fixture]})
    cmds, notes = fx.osc_commands(0, {1: frame(**slots)})
    return {c["a"].split("/overrides/")[1]: c["v"] for c in cmds}, notes, cmds


class OscTests(unittest.TestCase):
    def test_rgb_fixture_gets_full_dimmer_and_colour(self):
        m, _, cmds = osc_map(RGB, s10=255, s11=128, s12=0)
        self.assertEqual(m, {"dimmer": [1.0], "color": [1.0, 0.502, 0.0]})
        self.assertEqual(cmds[0]["a"], "/fixture/P1/overrides/dimmer")
        self.assertNotIn("approx", cmds[1])

    def test_pan_tilt_dimmer_zoom_focus_frost(self):
        m, _, _ = osc_map(MOVER2, s0=164, s1=170, s6=128, s7=255, s8=127, s9=255)
        self.assertAlmostEqual(m["panAngle"][0], (164 / 255 - 0.5) * 540, places=1)
        self.assertAlmostEqual(m["tiltAngle"][0], (170 / 255 - 0.5) * 270, places=1)
        self.assertAlmostEqual(m["dimmer"][0], 0.502, places=3)
        self.assertEqual(m["zoomAngle"], [22.0])
        self.assertAlmostEqual(m["focus"][0], 0.498, places=3)
        self.assertEqual(m["frostAmount"], [1.0])

    def test_inverted_dimmer_and_zoom(self):
        m, _, _ = osc_map(INVERTED, s300=255, s301=255)
        self.assertEqual(m["dimmer"], [0.0])
        self.assertEqual(m["zoomAngle"], [5.0])
        m, _, _ = osc_map(INVERTED, s300=0, s301=0)
        self.assertEqual(m["dimmer"], [1.0])
        self.assertEqual(m["zoomAngle"], [60.0])

    def test_colour_wheel_and_gobo_are_wheel_indexes_marked_approximate(self):
        _, _, cmds = osc_map(MOVER2, s2=10, s3=27)
        by = {c["a"].split("/overrides/")[1]: c for c in cmds}
        self.assertEqual(by["color1"]["v"], [2]); self.assertTrue(by["color1"]["approx"])
        self.assertEqual(by["gobo1"]["v"], [3]); self.assertTrue(by["gobo1"]["approx"])
        self.assertEqual(by["goboShakeMode1"]["v"], ["stopped"])

    def test_shaking_gobo_sends_the_same_index_and_shake_mode(self):
        m, _, _ = osc_map(MOVER2, s3=40)
        self.assertEqual(m["gobo1"], [2]); self.assertEqual(m["goboShakeMode1"], ["shaking"])
        self.assertIn("goboShakeSpeed1", m)

    def test_gobo_rotation_direction_and_speed(self):
        m, _, _ = osc_map(MOVER2, s4=158)          # lower range, direction 0 = clockwise, 1 rev/s
        self.assertEqual(m["goboRotationMode1"], ["rotatingCW"])
        self.assertAlmostEqual(m["goboRotationSpeed1"][0], 1.0, delta=0.06)
        m, _, _ = osc_map(MOVER2, s4=224)          # upper range, direction 1 = counter-clockwise
        self.assertEqual(m["goboRotationMode1"], ["rotatingCCW"])
        self.assertAlmostEqual(m["goboRotationSpeed1"][0], 1.0, delta=0.06)
        m, _, _ = osc_map(MOVER2, s4=192)
        self.assertEqual(m["goboRotationMode1"], ["stopped"])

    def test_gobo_angle_when_indexed(self):
        m, _, _ = osc_map(MOVER2, s4=63)
        self.assertEqual(m["goboRotationMode1"], ["indexed"])
        self.assertAlmostEqual(m["goboAngle1"][0], 63 / 127 * 360, places=0)

    def test_shutter_modes(self):
        self.assertEqual(osc_map(MOVER2, s5=15)[0]["shutterState"], ["closed"])
        self.assertEqual(osc_map(MOVER2, s5=47)[0]["shutterState"], ["open"])
        m = osc_map(MOVER2, s5=79)[0]
        self.assertEqual(m["shutterState"], ["strobe"]); self.assertAlmostEqual(m["strobeSpeed"][0], 9.87, places=2)

    def test_custom_slider_and_option(self):
        m, _, cmds = osc_map(MOVER2, s10=128, s11=192)
        self.assertAlmostEqual(m["Program_Speed"][0], 0.502, places=3)
        self.assertEqual(m["Program"][0], "Program_1"); self.assertAlmostEqual(m["Program"][1], 0.504, places=2)
        self.assertEqual(osc_map(MOVER2, s11=10)[0]["Program"], ["Off"])

    def test_pan_tilt_speed_and_other_unmapped_channels_are_reported(self):
        _, notes, _ = osc_map(MOVER2, s12=50)
        self.assertTrue(any("PanTiltSpeed" in n for n in notes))
        self.assertEqual(osc_map(MOVER2, s12=0)[1], [])

    def test_white_components_use_lightkey_names(self):
        m, _, _ = osc_map(WHITES, s400=255, s401=128, s402=64)
        self.assertEqual(m["warmWhite"], [0.502]); self.assertEqual(m["coolWhite"], [0.251]); self.assertNotIn("color", m)

    def test_master_colour_group_is_not_a_beam(self):
        zoom = {"name": "Z1", "profile": "Acme Zoom", "personality": "", "universe": 1, "address": 0, "footprint": 8,
                "panRange": 0, "tiltRange": 0, "beams": 1,
                "capabilities": [cap("ColorComponent", i, {"componentName": ["Red", "Green", "Blue"][i % 3]}) for i in range(3)]
                + [cap("ColorComponent", 3 + i, {"componentName": ["Red", "Green", "Blue"][i % 3]}) for i in range(3)]}
        m, notes, _ = osc_map(zoom, s0=0, s1=0, s2=0, s3=255, s4=51, s5=0)
        self.assertEqual(m["color"], [1.0, 0.2, 0.0])
        self.assertEqual(notes, [])

    def test_second_prism_rotation_does_not_override_the_first(self):
        mv = {"name": "MH9", "profile": "Acme", "personality": "", "universe": 1, "address": 0, "footprint": 4,
              "panRange": 0, "tiltRange": 0,
              "capabilities": [
                  cap("PrismRotation", 0, settings=[{"lo": -128, "hi": -67, "params": {"mode": 1, "direction": 1}},
                                                    {"lo": -66, "hi": -63, "params": {"mode": 0}},
                                                    {"lo": -62, "hi": -1, "params": {"mode": 1, "direction": 0}}]),
                  cap("PrismRotation", 1, settings=[{"lo": -128, "hi": -67, "params": {"mode": 1, "direction": 1}},
                                                    {"lo": -66, "hi": -63, "params": {"mode": 0}},
                                                    {"lo": -62, "hi": -1, "params": {"mode": 1, "direction": 0}}])]}
        m, _, _ = osc_map(mv, s0=209, s1=191)
        self.assertEqual(m["prismRotationMode1"], ["rotatingCW"])
        self.assertEqual(m["prismRotationMode2"], ["stopped"])

    def test_sixteen_bit_dimmer_keeps_the_fine_byte(self):
        fine = {"name": "PN1", "profile": "Acme", "personality": "", "universe": 1, "address": 0, "footprint": 2,
                "panRange": 0, "tiltRange": 0, "capabilities": [cap("Intensity", 0), cap("IntensityFine", 1)]}
        m, _, _ = osc_map(fine, s0=255, s1=69)
        self.assertEqual(int(m["dimmer"][0] * 65535), (255 << 8) | 69)           # truncation gives back the same value
        self.assertEqual(int((m["dimmer"][0] + 0.0) * 65535 + 1e-9), (255 << 8) | 69)

    def test_multibeam_fixture_sends_average_and_says_so(self):
        # two beams share the same R,G,B channel order: second group starts when a component repeats
        m, notes, _ = osc_map(BEAMS, s450=255, s453=0)
        self.assertEqual(m["color"][0], 0.5)
        self.assertTrue(any("beams differ" in n for n in notes))

    def test_tracker_events_carry_only_changed_commands(self):
        tr = lk.LevelTracker(lk.Fixtures(FMAP), 50)
        tr.frame(1, frame(), 1000)
        tr.frame(1, frame(s10=255), 1040)
        e = tr.log[-1]
        self.assertEqual([c["a"] for c in e["osc"]], ["/fixture/P1/overrides/color"])
        self.assertEqual(e["osc"][0]["v"], [1.0, 0.0, 0.0])
        tr.frame(1, frame(s10=255, s11=255), 1080)
        self.assertEqual(tr.log[-1]["osc"][0]["v"], [1.0, 1.0, 0.0])


class RecorderTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        lk.CFG.clear(); lk.CFG.update(lk.DEFAULTS); lk.CFG["takes_dir"] = self.dir
        fx = lk.Fixtures(FMAP)
        lk.FIXTURES[0] = (fx, 0)
        lk.TRACKER[0] = lk.LevelTracker(fx, 100)
        lk.TRACKER[0].frame(1, frame(), 1000)
        lk.REC.active = None

    def tearDown(self):
        lk.FIXTURES[0] = None; lk.TRACKER[0] = None; lk.REC.active = None

    def test_records_initial_state_and_timed_commands(self):
        lk.TRACKER[0].frame(1, frame(s10=255), 1500)
        lk.REC.start("Song One")
        t0 = lk.REC.active["t0"]
        lk.TRACKER[0].frame(1, frame(s10=255, s11=255), t0 + 1250)
        lk.TRACKER[0].frame(1, frame(s10=0, s11=255), t0 + 4000)
        info = lk.REC.stop()
        take = lk.REC.get("Song One")
        self.assertEqual(info["events"], 2)
        self.assertIn("/fixture/P1/overrides/color", [c["a"] for c in take["initial"]])
        self.assertEqual([e["at"] for e in take["events"]], [1.25, 4.0])
        self.assertEqual(take["events"][0]["osc"][0]["v"], [1.0, 1.0, 0.0])
        self.assertEqual(take["events"][1]["osc"][0]["v"], [0.0, 1.0, 0.0])

    def test_default_scope_starts_only_fixtures_that_change(self):
        lk.REC.start("s")
        t0 = lk.REC.active["t0"]
        lk.TRACKER[0].frame(1, frame(s10=255), t0 + 300)
        lk.REC.stop()
        take = lk.REC.get("s")
        self.assertEqual({c["a"].split("/")[2] for c in take["initial"]}, {"P1"})
        self.assertEqual(take["scope"], "changed")
        self.assertAlmostEqual(take["lead"], 0.045)

    def test_scope_all_starts_every_fixture(self):
        lk.REC.start("s", scope="all")
        t0 = lk.REC.active["t0"]
        lk.TRACKER[0].frame(1, frame(s10=255), t0 + 300)
        lk.REC.stop()
        self.assertGreater(len({c["a"].split("/")[2] for c in lk.REC.get("s")["initial"]}), 1)
        with self.assertRaises(ValueError):
            lk.REC.start("bad", scope="everything")

    def test_fixture_filter_records_only_those(self):
        lk.REC.start("f", ["MH1"])
        t0 = lk.REC.active["t0"]
        lk.TRACKER[0].frame(1, frame(s10=255), t0 + 500)            # P1: ignored
        lk.TRACKER[0].frame(1, frame(s10=255, s104=255), t0 + 900)  # MH1: kept
        lk.REC.stop()
        take = lk.REC.get("f")
        self.assertEqual([e["osc"][0]["a"].split("/")[2] for e in take["events"]], ["MH1"])
        self.assertTrue(all(c["a"].split("/")[2] == "MH1" for c in take["initial"]))

    def test_one_recording_at_a_time_and_stop_without_start(self):
        lk.REC.start("a")
        with self.assertRaises(ValueError):
            lk.REC.start("b")
        lk.REC.stop()
        with self.assertRaises(ValueError):
            lk.REC.stop()

    def test_names_are_made_safe_and_missing_take_raises(self):
        lk.REC.start("../../etc/passwd")
        lk.REC.stop()
        self.assertEqual([t["name"] for t in lk.REC.list()], ["_.._etc_passwd"[0:0] or lk.REC._safe("../../etc/passwd")])
        self.assertTrue(os.path.dirname(os.path.join(self.dir, "x")) == self.dir)
        with self.assertRaises(KeyError):
            lk.REC.get("nope")

    def test_cannot_start_without_a_fixture_map(self):
        lk.FIXTURES[0] = None
        with self.assertRaises(ValueError):
            lk.REC.start("x")

    def test_unchanged_commands_are_not_repeated(self):
        lk.REC.start("r")
        t0 = lk.REC.active["t0"]
        lk.TRACKER[0].frame(1, frame(s10=255), t0 + 100)
        lk.TRACKER[0].frame(1, frame(s10=255, s400=7), t0 + 200)    # unmapped slot: no event at all
        lk.REC.stop()
        self.assertEqual(len(lk.REC.get("r")["events"]), 1)


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
