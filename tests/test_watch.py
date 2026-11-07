import importlib.util, json, os, tempfile, threading, unittest, urllib.error, urllib.request

HERE = os.path.dirname(__file__)
spec = importlib.util.spec_from_file_location("lightkey_api", os.path.join(HERE, "..", "lightkey_api.py"))
lk = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lk)
spec2 = importlib.util.spec_from_file_location("test_fixtures", os.path.join(HERE, "test_fixtures.py"))
tf = importlib.util.module_from_spec(spec2)
spec2.loader.exec_module(tf)


def slurp(path):
    with open(path) as f:
        return f.read()


class FakeStat:
    def __init__(self, mtime, size):
        self.st_mtime, self.st_size = mtime, size


class Rig(unittest.TestCase):
    """A fake Lightkey Mac: a temp HOME, a fixtures.json in it, and fake process / project / file calls."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = self.tmp.name
        self.saved_home, self.saved_io = lk.HOME, dict(lk.IO)
        self.saved_watch, self.saved_now = lk.WATCH, lk.now_ms
        lk.HOME = self.home
        lk.CFG.clear(); lk.CFG.update(lk.DEFAULTS)
        lk.CFG["fixtures_file"] = os.path.join(self.home, "Library", "LightkeyAPI", "fixtures.json")
        lk.FIXTURES[0] = None; lk.TRACKER[0] = None; lk.LEVELS.clear()
        self.clock = [1794000000000]
        lk.now_ms = lambda: self.clock[0]
        self.running = True
        self.project = os.path.join(self.home, "Documents", "Show.lightkeyproj")
        self.files = {self.project: (100.0, 5000, [tf.RGB, tf.MOVER])}     # path -> (mtime, size, fixtures)
        self.parsed = []
        self.stat_error = self.parse_error = None
        lk.IO.update(running=lambda: self.running, find=lambda: self.project, stat=self._stat, parse=self._parse,
                     listdir=lambda d: [])
        lk.WATCH = self.w = lk.Watcher()

    def tearDown(self):
        lk.HOME = self.saved_home
        lk.IO.clear(); lk.IO.update(self.saved_io)
        lk.WATCH, lk.now_ms = self.saved_watch, self.saved_now
        lk.FIXTURES[0] = None; lk.TRACKER[0] = None
        self.tmp.cleanup()

    def _stat(self, path):
        if self.stat_error:
            raise self.stat_error
        m, s, _ = self.files[path]
        return FakeStat(m, s)

    def _parse(self, path):
        self.parsed.append(path)
        if self.parse_error:
            raise self.parse_error
        return {"version": 1, "project": os.path.basename(path), "fixtures": self.files[path][2]}

    def edit(self, fixtures, mtime=None, size=None, path=None):
        path = path or self.project
        m, s, _ = self.files.get(path, (100.0, 5000, None))
        self.files[path] = (mtime if mtime is not None else m + 60, size if size is not None else s + 1, fixtures)

    def saved(self):
        with open(lk.CFG["fixtures_file"]) as f:
            return json.load(f)

    def age_map(self):
        """Make the written fixtures.json look old, so a rewrite shows up as a new map id."""
        os.utime(lk.CFG["fixtures_file"], (1_700_000_000, 1_700_000_000))
        lk.FIXTURES[0] = None; lk.load_fixtures()


class WatcherStateTests(Rig):
    def test_first_read_writes_the_map_and_the_source_signature(self):
        r = self.w.check()
        self.assertEqual((r["ok"], r["project"], r["count"], r["changed"]), (True, "Show.lightkeyproj", 2, True))
        self.assertEqual(self.w.report()["state"], "changed")
        self.assertEqual(self.saved()["source"], {"path": self.project, "mtime": 100.0, "size": 5000})
        self.assertEqual(len(lk.FIXTURES[0][0].fixtures), 2)                 # loaded without a restart
        self.assertEqual(r["map"], int(lk.FIXTURES[0][1]))

    def test_unchanged_project_is_not_read_again(self):
        self.w.check(); self.parsed.clear()
        r = self.w.check()
        self.assertEqual(self.parsed, [])
        self.assertFalse(r["changed"])
        self.assertEqual(self.w.report()["state"], "ok")

    def test_another_project_replaces_the_map(self):
        self.w.check(); self.age_map(); old = int(lk.FIXTURES[0][1])
        other = os.path.join(self.home, "Downloads", "Other.lightkeyproj")
        self.files[other] = (7.0, 900, [tf.RGBW])
        self.project = other
        r = self.w.check()
        self.assertEqual((r["project"], r["count"], r["changed"]), ("Other.lightkeyproj", 1, True))
        self.assertNotEqual(r["map"], old)
        self.assertEqual(self.w.report()["open_project"], "Other.lightkeyproj")

    def test_same_project_with_a_new_fixture(self):
        self.w.check(); self.age_map(); old = int(lk.FIXTURES[0][1])
        self.edit([tf.RGB, tf.MOVER, tf.RGBW])
        r = self.w.check()
        self.assertEqual((r["count"], r["changed"]), (3, True))
        self.assertNotEqual(r["map"], old)

    def test_saving_with_no_patch_change_keeps_the_map_id_and_file(self):
        self.w.check(); self.age_map()
        before = os.stat(lk.CFG["fixtures_file"]).st_mtime_ns
        body, map_id = slurp(lk.CFG["fixtures_file"]), int(lk.FIXTURES[0][1])
        self.edit([tf.RGB, tf.MOVER])                                        # new mtime and size, same fixtures
        r = self.w.check()
        self.assertEqual(len(self.parsed), 2)                                # it did look
        self.assertEqual((r["changed"], r["map"]), (False, map_id))
        self.assertEqual(os.stat(lk.CFG["fixtures_file"]).st_mtime_ns, before)
        self.assertEqual(slurp(lk.CFG["fixtures_file"]), body)
        self.assertEqual(self.w.report()["state"], "ok")
        self.w.check()
        self.assertEqual(len(self.parsed), 2)                                # and does not look again until it changes

    def test_a_map_from_an_older_agent_is_not_rewritten_when_nothing_changed(self):
        os.makedirs(os.path.dirname(lk.CFG["fixtures_file"]))
        with open(lk.CFG["fixtures_file"], "w") as f:                        # 0.4.x: no source key
            json.dump(lk.compact_map({"version": 1, "project": "Show.lightkeyproj", "fixtures": [tf.RGB, tf.MOVER]}), f)
        os.utime(lk.CFG["fixtures_file"], (1_700_000_000, 1_700_000_000))
        r = self.w.check()
        self.assertFalse(r["changed"]); self.assertNotIn("source", self.saved())
        self.assertEqual(r["map"], 1_700_000_000)

    def test_lightkey_closed_keeps_the_map(self):
        self.w.check(); self.running = False
        r = self.w.check()
        self.assertEqual(r["code"], "lightkey_closed")
        self.assertEqual(self.w.report()["state"], "lightkey_closed")
        self.assertEqual(len(lk.FIXTURES[0][0].fixtures), 2)
        self.running = True
        self.assertTrue(self.w.check()["ok"]); self.assertEqual(self.w.report()["state"], "ok")

    def test_no_project_keeps_the_map(self):
        self.w.check(); lk.IO["find"] = lambda: None
        r = self.w.check()
        self.assertEqual(r["code"], "no_project")
        self.assertEqual((self.w.report()["state"], self.w.report()["open_project"]), ("no_project", None))
        self.assertEqual(len(lk.FIXTURES[0][0].fixtures), 2)

    def test_unreadable_file_keeps_the_old_map_and_retries(self):
        self.w.check(); self.edit([tf.RGB, tf.MOVER, tf.RGBW])
        self.parse_error = ValueError("half written")
        r = self.w.check()
        self.assertEqual(r["code"], "unreadable")
        self.assertEqual(len(lk.FIXTURES[0][0].fixtures), 2); self.assertEqual(len(self.saved()["fixtures"]), 2)
        self.parse_error = None
        self.assertEqual(self.w.check()["count"], 3)                        # the next cycle tries again

    def test_missing_project_file_is_unreadable(self):
        self.stat_error = FileNotFoundError("gone")
        self.assertEqual(self.w.check()["code"], "unreadable")

    def test_permission_denied_then_recovery(self):
        self.parse_error = PermissionError(1, "Operation not permitted")
        r = self.w.check()
        want = ("macOS has not allowed this helper to read your Documents folder. Open System Settings > Privacy & Security > "
                "Files and Folders and allow python3, then press Read fixtures again.")
        self.assertEqual(r, {"ok": False, "code": "permission_denied", "folder": "Documents", "error": want})
        rep = self.w.report()
        self.assertEqual((rep["state"], rep["folder"], rep["detail"], rep["folders"]["Documents"]), ("permission_denied", "Documents", want, "denied"))
        self.parse_error = None
        r = self.w.check()
        self.assertTrue(r["ok"]); self.assertEqual(self.w.report()["state"], "changed")
        self.assertEqual(self.w.report()["folders"]["Documents"], "allowed")
        self.assertIsNone(self.w.report()["detail"])

    def test_permission_denied_on_stat_in_downloads(self):
        self.project = os.path.join(self.home, "Downloads", "Show.lightkeyproj")
        self.files[self.project] = (1.0, 1, [tf.RGB])
        self.stat_error = PermissionError(1, "no")
        r = self.w.check()
        self.assertEqual((r["code"], r["folder"]), ("permission_denied", "Downloads"))

    def test_denied_folder_is_probed_again_each_cycle(self):
        self.w.folders["Documents"] = "denied"
        self.w.check()
        self.assertEqual(self.w.folders["Documents"], "allowed")

    def test_project_outside_documents_and_downloads_has_no_folder_name(self):
        self.project = os.path.join(self.home, "Desktop", "Show.lightkeyproj")
        self.files[self.project] = (1.0, 1, [tf.RGB])
        self.parse_error = PermissionError(1, "no")
        r = self.w.check()
        self.assertIsNone(r["folder"]); self.assertIn("the folder that holds your project", r["error"])

    def test_a_crash_inside_a_check_is_contained(self):
        lk.IO["find"] = lambda: 1 / 0
        r = self.w.check()
        self.assertEqual(r["code"], "unreadable")

    def test_outputs_never_carry_a_path_or_user_name(self):
        self.w.check()
        blob = json.dumps([self.w.report(), self.w.fixture_map(), self.w.check(force=True)])
        self.assertNotIn(self.home, blob); self.assertNotIn(os.path.basename(self.home), blob)

    def test_last_check_time_comes_from_the_clock(self):
        self.assertIsNone(self.w.report()["last_check_at"])
        self.w.check()
        self.assertEqual(self.w.report()["last_check_at"], self.clock[0])


class WriteAndHelloTests(Rig):
    def test_write_is_atomic_a_failed_rename_leaves_the_old_map_in_use(self):
        self.w.check(); good = slurp(lk.CFG["fixtures_file"])
        self.edit([tf.RGB])
        real = os.replace
        def boom(a, b): raise OSError("disk full")
        os.replace = boom
        try:
            r = self.w.check()
        finally:
            os.replace = real
        self.assertEqual(r["code"], "unreadable")
        self.assertEqual(slurp(lk.CFG["fixtures_file"]), good)
        self.assertEqual(len(lk.FIXTURES[0][0].fixtures), 2)

    def test_write_goes_through_a_temp_file_next_to_the_map(self):
        seen = []
        real = os.replace
        def spy(a, b): seen.append((a, b)); real(a, b)
        os.replace = spy
        try:
            self.w.check()
        finally:
            os.replace = real
        self.assertEqual(seen, [(lk.CFG["fixtures_file"] + ".tmp", lk.CFG["fixtures_file"])])
        self.assertFalse(os.path.exists(lk.CFG["fixtures_file"] + ".tmp"))

    def test_the_relay_hello_carries_the_new_map_after_a_re_read(self):
        relay = lk.Relay()
        def hello():
            _, addr, args = lk.osc_parse(relay._hello())[0]
            return addr, args[2:]
        self.assertEqual(hello()[1], ["", 0, 0])
        self.w.check(); self.age_map()
        first = hello()[1]
        self.assertEqual(first, ["Show.lightkeyproj", 2, 1_700_000_000])
        self.edit([tf.RGB, tf.MOVER])                                        # saved, same fixtures: the same hello
        self.w.check()
        self.assertEqual(hello()[1], first)
        self.edit([tf.RGB, tf.MOVER, tf.RGBW])
        self.w.check()
        name, count, mid = hello()[1]
        self.assertEqual((name, count), ("Show.lightkeyproj", 3)); self.assertNotEqual(mid, 1_700_000_000)

    def test_export_fixtures_records_the_source_so_the_first_check_does_not_re_read(self):
        project = os.path.join(self.home, "Documents", "Show.lightkeyproj")
        os.makedirs(os.path.dirname(project))
        with open(project, "wb") as f:
            f.write(b"x")
        real = lk.parse_project
        lk.parse_project = lambda p: {"version": 1, "project": "Show.lightkeyproj", "fixtures": [tf.RGB, tf.MOVER]}
        try:
            lk.export_fixtures(project=project)
        finally:
            lk.parse_project = real
        st = os.stat(project)
        self.assertEqual(self.saved()["source"], {"path": project, "mtime": st.st_mtime, "size": st.st_size})
        lk.IO["stat"] = os.stat
        self.assertFalse(self.w.check()["changed"]); self.assertEqual(self.parsed, [])


class IntervalTests(unittest.TestCase):
    def setUp(self):
        lk.CFG.clear(); lk.CFG.update(lk.DEFAULTS)

    def test_default_is_180_and_documented_in_the_config_defaults(self):
        self.assertEqual(lk.DEFAULTS["project_check_s"], 180)
        self.assertEqual(lk.project_check_interval(), 180)

    def test_never_below_30(self):
        for v, want in ((10, 30), (0, 30), (-5, 30), (30, 30), (45, 45), (600, 600), ("90", 90)):
            lk.CFG["project_check_s"] = v
            self.assertEqual(lk.project_check_interval(), want, v)

    def test_nonsense_falls_back_to_the_default(self):
        for v in ("soon", None, [1]):
            lk.CFG["project_check_s"] = v
            self.assertEqual(lk.project_check_interval(), 180, v)

    def test_version(self):
        self.assertEqual(lk.__version__, "0.5.0")


class PrimingTests(Rig):
    def test_first_start_lists_both_folders_once_and_remembers_the_outcome(self):
        calls = []
        lk.IO["listdir"] = lambda d: calls.append(os.path.basename(d)) or []
        self.w.prime_folders()
        self.assertEqual(calls, ["Documents", "Downloads"])
        self.assertEqual(self.w.report()["folders"], {"Documents": "allowed", "Downloads": "allowed"})
        self.assertTrue(os.path.exists(os.path.join(os.path.dirname(lk.CFG["fixtures_file"]), "tcc_primed")))
        calls.clear()
        w2 = lk.Watcher(); w2.prime_folders()                                # a later start
        self.assertEqual(calls, [])
        self.assertEqual(w2.report()["folders"]["Documents"], "allowed")

    def test_denied_and_missing_folders(self):
        def listdir(d):
            if d.endswith("Documents"): raise PermissionError(1, "no")
            raise FileNotFoundError(d)
        lk.IO["listdir"] = listdir
        self.w.prime_folders()
        self.assertEqual(self.w.report()["folders"], {"Documents": "denied", "Downloads": "unknown"})

    def test_removing_the_flag_primes_again(self):
        self.w.prime_folders()
        os.remove(os.path.join(os.path.dirname(lk.CFG["fixtures_file"]), "tcc_primed"))
        calls = []
        lk.IO["listdir"] = lambda d: calls.append(d) or []
        lk.Watcher().prime_folders()
        self.assertEqual(len(calls), 2)

    def test_folders_are_unknown_before_priming(self):
        self.assertEqual(self.w.report()["folders"], {"Documents": "unknown", "Downloads": "unknown"})


class HttpTests(Rig):
    def setUp(self):
        super().setUp()
        lk.CFG["allow_interfaces"] = []
        self.srv = lk.Server(("127.0.0.1", 0), lk.Handler)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.base = "http://127.0.0.1:%d" % self.srv.server_address[1]

    def tearDown(self):
        self.srv.shutdown(); self.srv.server_close()
        super().tearDown()

    def req(self, path, method="GET", data=None):
        r = urllib.request.Request(self.base + path, method=method, data=data)
        try:
            with urllib.request.urlopen(r, timeout=5) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read())
            finally:
                e.close()

    def test_refresh_200_changed_then_unchanged(self):
        code, body = self.req("/fixtures/refresh", "POST", b"")
        self.assertEqual(code, 200)
        self.assertEqual(body, {"ok": True, "project": "Show.lightkeyproj", "count": 2, "map": body["map"], "changed": True})
        self.assertEqual(body["map"], int(lk.FIXTURES[0][1]))
        before = os.stat(lk.CFG["fixtures_file"]).st_mtime_ns
        code, body2 = self.req("/fixtures/refresh", "POST")
        self.assertEqual((code, body2["changed"], body2["map"]), (200, False, body["map"]))
        self.assertEqual(os.stat(lk.CFG["fixtures_file"]).st_mtime_ns, before)

    def test_refresh_reads_again_even_when_the_signature_matches(self):
        self.req("/fixtures/refresh", "POST"); self.parsed.clear()
        self.req("/fixtures/refresh", "POST")
        self.assertEqual(len(self.parsed), 1)

    def test_refresh_picks_up_a_fixture_added_with_the_same_file_signature(self):
        self.req("/fixtures/refresh", "POST")
        self.files[self.project] = (100.0, 5000, [tf.RGB, tf.MOVER, tf.RGBW])
        code, body = self.req("/fixtures/refresh", "POST")
        self.assertEqual((code, body["count"], body["changed"]), (200, 3, True))

    def test_refresh_409_lightkey_closed(self):
        self.running = False
        code, body = self.req("/fixtures/refresh", "POST")
        self.assertEqual((code, body["ok"], body["code"]), (409, False, "lightkey_closed")); self.assertTrue(body["error"])

    def test_refresh_409_no_project(self):
        lk.IO["find"] = lambda: None
        code, body = self.req("/fixtures/refresh", "POST")
        self.assertEqual((code, body["code"]), (409, "no_project"))

    def test_refresh_409_busy_when_a_check_is_running(self):
        with self.w.lock:
            code, body = self.req("/fixtures/refresh", "POST")
        self.assertEqual((code, body["ok"], body["code"]), (409, False, "busy"))
        self.assertEqual(self.req("/fixtures/refresh", "POST")[0], 200)      # and it is free again afterwards

    def test_refresh_403_permission_denied_with_the_exact_text(self):
        self.parse_error = PermissionError(1, "no")
        code, body = self.req("/fixtures/refresh", "POST")
        self.assertEqual(code, 403)
        self.assertEqual(body, {"ok": False, "code": "permission_denied", "folder": "Documents", "error":
                                "macOS has not allowed this helper to read your Documents folder. Open System Settings > Privacy & Security > "
                                "Files and Folders and allow python3, then press Read fixtures again."})

    def test_refresh_422_unreadable(self):
        self.parse_error = ValueError("bad plist")
        code, body = self.req("/fixtures/refresh", "POST")
        self.assertEqual((code, body["ok"], body["code"]), (422, False, "unreadable"))
        self.assertNotIn("bad plist", json.dumps(body))

    def test_refresh_from_a_disallowed_interface_is_refused(self):
        orig = lk._allowed_ips
        lk._allowed_ips = lambda: {"10.9.9.9"}
        try:
            code, body = self.req("/fixtures/refresh", "POST")
        finally:
            lk._allowed_ips = orig
        self.assertEqual(code, 403); self.assertEqual(self.parsed, [])

    def test_refresh_is_listed_in_the_index(self):
        self.assertIn("POST /fixtures/refresh", self.req("/")[1]["endpoints"])

    def test_other_posts_still_work(self):
        self.assertEqual(self.req("/nope", "POST", b"{}")[0], 404)
        self.assertEqual(self.req("/record/stop", "POST", b"{}")[0], 409)

    def test_health_keeps_the_old_keys_and_adds_the_new_ones(self):
        self.w.check()
        code, h = self.req("/health")
        self.assertEqual(code, 200)
        for k in ("ok", "lightkey", "error", "dmx_age_ms"):
            self.assertIn(k, h)
        self.assertEqual(h["version"], "0.5.0")
        self.assertEqual({k: h["fixture_map"][k] for k in ("project", "count", "map")}, {"project": "Show.lightkeyproj", "count": 2, "map": int(lk.FIXTURES[0][1])})
        self.assertIsInstance(h["fixture_map"]["read_at"], int)
        w = h["watch"]
        self.assertEqual((w["interval_s"], w["open_project"], w["state"], w["folder"], w["detail"]), (180, "Show.lightkeyproj", "changed", None, None))
        self.assertEqual(set(w), {"enabled", "interval_s", "last_check_at", "open_project", "state", "folder", "detail", "folders"})
        self.assertEqual(w["folders"], {"Documents": "allowed", "Downloads": "unknown"})
        self.assertNotIn(self.home, json.dumps(h))

    def test_health_without_a_map_has_null_fixture_map(self):
        self.assertIsNone(self.req("/health")[1]["fixture_map"])

    def test_health_reports_permission_denied(self):
        self.parse_error = PermissionError(1, "no"); self.w.check()
        w = self.req("/health")[1]["watch"]
        self.assertEqual((w["state"], w["folder"]), ("permission_denied", "Documents"))
        self.assertIn("Files and Folders", w["detail"])


if __name__ == "__main__":
    unittest.main()
