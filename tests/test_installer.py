"""install.sh run with sh in a temp HOME on a stubbed Mac: route, launchctl, plutil, curl and ifconfig are fakes on PATH."""
import hashlib, json, os, shutil, subprocess, sys, tempfile, unittest

ROOT = os.path.join(os.path.dirname(__file__), "..")
SENTENCE = "macOS may ask to let Lightkey API use your local network and read your Documents and Downloads folders. Click Allow each time. The folders are only used to find your Lightkey project."
HEALTH = json.dumps({"ok": True, "watch": {"folders": {"Documents": "allowed", "Downloads": "allowed"}}})


def sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = self.tmp.name
        self.home, self.src, self.stubs = os.path.join(t, "home"), os.path.join(t, "src"), os.path.join(t, "stubs")
        for d in (self.home, self.src, self.stubs):
            os.makedirs(d)
        self.log = os.path.join(t, "calls.log")
        self.iface = "en7"
        for f in ("install.sh", "uninstall.sh", "lightkey_api.py", "lightkey_cues.py"):
            shutil.copy(os.path.join(ROOT, f), self.src)
        self.sums()
        self.stub("route", 'echo "route $*" >> "$LOG"\n[ -n "$IFACE" ] && printf "   route to: x\\n  interface: %s\\n" "$IFACE"\nexit 0')
        self.stub("launchctl", 'echo "launchctl $*" >> "$LOG"')
        self.stub("plutil", "exit 0")
        self.stub("ifconfig", "exit 0")
        self.stub("ifconfig_all", 'cat <<"EOT"\nlo0: flags=8049<UP,LOOPBACK,RUNNING,MULTICAST> mtu 16384\n\tinet 127.0.0.1 netmask 0xff000000\n\tinet6 ::1 prefixlen 128\nen0: flags=8863<UP,BROADCAST,SMART,RUNNING,SIMPLEX,MULTICAST> mtu 1500\n\tether aa:bb:cc:dd:ee:ff\n\tinet 10.40.0.44 netmask 0xffffff00 broadcast 10.40.0.255\nen5: flags=8863<UP,BROADCAST,SMART,RUNNING,SIMPLEX,MULTICAST> mtu 1500\n\tinet6 fe80::1%en5 prefixlen 64 scopeid 0x9\n\tinet 10.30.0.211 netmask 0xffffff00 broadcast 10.30.0.255\nEOT')
        self.stub("cc", 'echo "cc $*" >> "$LOG"\n[ -z "$CC_FAIL" ] || { echo "no compiler" >&2; exit 1; }\nwhile [ $# -gt 0 ]; do [ "$1" = "-o" ] && { printf "#!/bin/sh\\n" > "$2"; chmod +x "$2"; }; shift; done')
        self.stub("codesign", 'echo "codesign $*" >> "$LOG"')
        self.stub("tccutil", 'echo "tccutil $*" >> "$LOG"')
        self.stub("curl", 'echo "curl $*" >> "$LOG"\ncase "$*" in *127.0.0.1:8770/health*) echo \'%s\' ;; *) exit 22 ;; esac' % HEALTH)

    def tearDown(self):
        self.tmp.cleanup()

    def stub(self, name, body):
        p = os.path.join(self.stubs, name)
        with open(p, "w") as f:
            f.write("#!/bin/sh\n" + body + "\n")
        os.chmod(p, 0o755)

    def sums(self, skip=()):
        with open(os.path.join(self.src, "SHA256SUMS"), "w") as f:
            for n in ("lightkey_api.py", "lightkey_cues.py", "install.sh", "uninstall.sh"):
                if n not in skip:
                    f.write("%s  %s\n" % (sha(os.path.join(self.src, n)), n))

    def app(self, *p):
        return os.path.join(self.home, "Library", "Application Support", "LightkeyAPI", *p)

    def run_install(self, *args, here=None, extra=None):
        env = dict(os.environ, HOME=self.home, LOG=self.log, IFACE=self.iface, LIGHTKEY_UNAME="Darwin", LIGHTKEY_PY=sys.executable,
                   LIGHTKEY_CC=os.path.join(self.stubs, "cc"), LIGHTKEY_IFCONFIG=os.path.join(self.stubs, "ifconfig_all"), LIGHTKEY_CODESIGN=os.path.join(self.stubs, "codesign"),
                   LIGHTKEY_TCCUTIL=os.path.join(self.stubs, "tccutil"), CC_FAIL=(extra or {}).get("CC_FAIL", ""),
                   LIGHTKEY_ROUTE=os.path.join(self.stubs, "route"), PATH=self.stubs + os.pathsep + os.environ["PATH"], TMPDIR=self.tmp.name)
        r = subprocess.run(["sh", "install.sh"] + list(args), cwd=here or self.src, env=env, capture_output=True, text=True, timeout=120)
        return r.returncode, r.stdout + r.stderr

    def calls(self):
        if not os.path.exists(self.log):
            return ""
        with open(self.log) as f:
            return f.read()

    def config(self):
        with open(self.app("config.json")) as f:
            return json.load(f)

    def test_shell_syntax(self):
        for f in ("install.sh", "uninstall.sh"):
            self.assertEqual(subprocess.run(["sh", "-n", os.path.join(ROOT, f)]).returncode, 0)

    def test_offline_install_works_and_never_touches_the_network(self):
        code, out = self.run_install()
        self.assertEqual(code, 0, out)
        self.assertIn("Nothing is downloaded", out)
        self.assertTrue(os.path.exists(self.app("lightkey_api.py")))
        self.assertEqual(sha(self.app("lightkey_api.py")), sha(os.path.join(self.src, "lightkey_api.py")))
        for line in self.calls().splitlines():
            if line.startswith("curl"):
                self.assertIn("127.0.0.1:8770/health", line)
        self.assertNotIn("releases/latest", self.calls())
        self.assertIn("lightkey-api 0.5.0 installed", out)

    def test_it_prints_the_folder_sentence_and_the_outcome(self):
        code, out = self.run_install()
        self.assertIn(SENTENCE, out)
        self.assertIn("folders: Documents allowed Downloads allowed", out)
        self.assertLess(out.index(SENTENCE), out.index("installed"))

    def test_folder_outcome_denied_says_where_to_click(self):
        self.stub("curl", 'case "$*" in *127.0.0.1:8770/health*) echo \'{"watch":{"folders":{"Documents":"denied","Downloads":"allowed"}}}\' ;; *) exit 22 ;; esac')
        code, out = self.run_install()
        self.assertIn("Files and Folders", out)
        self.assertIn("allow Lightkey API", out)

    def plist_args(self):
        import plistlib
        with open(os.path.join(self.home, "Library", "LaunchAgents", "io.github.lightkey-api.plist"), "rb") as f:
            return plistlib.load(f)["ProgramArguments"]

    def test_the_agent_starts_through_a_signed_launcher_app_so_macos_can_ask(self):
        code, out = self.run_install()
        self.assertEqual(code, 0, out)
        app = self.app("Lightkey API.app")
        self.assertTrue(os.access(os.path.join(app, "Contents", "MacOS", "lightkey-api"), os.X_OK))
        with open(os.path.join(app, "Contents", "Info.plist")) as f:
            info = f.read()
        for want in ("io.github.lightkey-api", "NSDocumentsFolderUsageDescription", "NSDownloadsFolderUsageDescription", "NSLocalNetworkUsageDescription", "LSUIElement"):
            self.assertIn(want, info)
        self.assertRegex(self.calls(), r"codesign --force --sign - .*Lightkey API\.app")
        self.assertEqual(self.plist_args(), [os.path.join(app, "Contents", "MacOS", "lightkey-api"), self.app("lightkey_api.py"), "run"])

    def test_rerun_keeps_the_same_launcher_so_macos_keeps_its_answers(self):
        self.run_install()
        self.run_install()
        self.assertEqual(self.calls().count("codesign "), 1)
        self.assertEqual(self.calls().count("cc "), 1)

    def test_a_failed_launcher_build_still_installs_and_says_so(self):
        code, out = self.run_install(extra={"CC_FAIL": "1"})
        self.assertEqual(code, 0, out)
        self.assertIn("Could not set up the Lightkey API launcher", out)
        self.assertFalse(os.path.exists(self.app("Lightkey API.app")))
        self.assertEqual(self.plist_args(), [sys.executable, "-u", self.app("lightkey_api.py"), "run"])

    def test_bootstrap_is_retried_when_launchd_is_still_removing_the_old_job(self):
        n = os.path.join(self.tmp.name, "n")
        self.stub("launchctl", 'echo "launchctl $*" >> "$LOG"\ncase "$1" in bootstrap) c=$(cat "%s" 2>/dev/null || echo 0); echo $((c+1)) > "%s"; [ "$c" -ge 2 ] || { echo "Bootstrap failed: 5: Input/output error" >&2; exit 5; } ;; esac' % (n, n))
        code, out = self.run_install()
        self.assertEqual(code, 0, out)
        self.assertEqual(self.calls().count("launchctl bootstrap"), 3)

    def test_a_refused_folder_is_forgotten_so_macos_asks_again(self):
        self.stub("curl", 'echo "curl $*" >> "$LOG"\ncase "$*" in *127.0.0.1:8770/health*) echo \'{"watch":{"folders":{"Documents":"allowed","Downloads":"denied"}}}\' ;; *) exit 22 ;; esac')
        code, out = self.run_install()
        self.assertIn("tccutil reset SystemPolicyDownloadsFolder io.github.lightkey-api", self.calls())
        self.assertNotIn("SystemPolicyDocumentsFolder", self.calls())

    def test_allowed_folders_are_not_reset(self):
        self.run_install()
        self.assertNotIn("tccutil", self.calls())

    def test_uninstall_removes_the_launcher_and_purge_forgets_the_answers(self):
        self.run_install()
        self.assertTrue(os.path.isdir(self.app("Lightkey API.app")))
        env = dict(os.environ, HOME=self.home, LOG=self.log, PATH=self.stubs + os.pathsep + os.environ["PATH"])
        subprocess.run(["sh", os.path.join(self.src, "uninstall.sh")], env=env, capture_output=True, text=True, timeout=60)
        self.assertFalse(os.path.exists(self.app("Lightkey API.app")))
        self.assertFalse(os.path.exists(self.app("launcher.stamp")))

    def test_the_prime_flag_is_removed_so_the_agent_asks_again_after_an_upgrade(self):
        os.makedirs(self.app())
        with open(self.app("tcc_primed"), "w") as f:
            f.write("{}")
        self.run_install()
        self.assertFalse(os.path.exists(self.app("tcc_primed")))

    def test_allow_for_uses_the_interface_that_route_finds(self):
        code, out = self.run_install("--allow-for", "10.30.0.5")
        self.assertEqual(code, 0, out)
        self.assertIn("route -n get 10.30.0.5", self.calls())
        self.assertEqual(self.config()["allow_interfaces"], ["en7"])
        self.assertIn("Allowing network interface en7", out)

    def test_allow_address_picks_the_interface_that_owns_that_address(self):
        code, out = self.run_install("--allow-address", "10.30.0.211")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.config()["allow_interfaces"], ["en5"])
        self.assertIn("Allowing network interface en5 (it has 10.30.0.211", out)

    def test_allow_address_wins_over_the_route_when_the_two_networks_differ(self):
        # The Mac reaches StagePilot through en7 (what route says) but StagePilot uses the en5 address.
        code, out = self.run_install("--allow-for", "10.20.0.103", "--allow-address", "10.30.0.211")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.config()["allow_interfaces"], ["en5"])
        self.assertNotIn("route -n get", self.calls())

    def test_an_address_this_mac_does_not_have_falls_back_to_the_route(self):
        code, out = self.run_install("--allow-for", "192.168.1.20", "--allow-address", "10.99.9.9")
        self.assertEqual(code, 0, out)
        self.assertIn("no interface with the address 10.99.9.9", out)
        self.assertEqual(self.config()["allow_interfaces"], [self.iface])

    def test_an_unknown_address_with_nothing_else_installs_nothing(self):
        code, out = self.run_install("--allow-address", "10.99.9.9")
        self.assertNotEqual(code, 0)
        self.assertIn("no network interface with the address 10.99.9.9", out)
        self.assertFalse(os.path.exists(self.app("lightkey_api.py")))

    def test_allow_address_needs_an_address(self):
        code, out = self.run_install("--allow-address")
        self.assertNotEqual(code, 0)
        self.assertIn("--allow-address needs", out)

    def test_allow_and_allow_for_together_and_no_duplicates(self):
        self.run_install("--allow", "en0", "--allow-for", "10.30.0.5")
        self.assertEqual(self.config()["allow_interfaces"], ["en0", "en7"])
        self.run_install("--allow", "en7", "--allow-for", "10.30.0.5")
        self.assertEqual(self.config()["allow_interfaces"], ["en0", "en7"])

    def test_allow_alone_is_unchanged(self):
        self.run_install("--allow", "en3")
        self.assertEqual(self.config()["allow_interfaces"], ["en3"])

    def test_allow_for_with_no_interface_fails_clearly_and_installs_nothing(self):
        self.iface = ""
        code, out = self.run_install("--allow-for", "10.30.0.5")
        self.assertEqual(code, 1)
        self.assertIn("Could not find a network interface that reaches 10.30.0.5", out)
        self.assertFalse(os.path.exists(self.app("lightkey_api.py")))

    def test_allow_for_needs_an_address(self):
        code, out = self.run_install("--allow-for")
        self.assertEqual(code, 1); self.assertIn("--allow-for needs", out)

    def test_offline_checksum_mismatch_aborts_before_installing(self):
        with open(os.path.join(self.src, "lightkey_api.py"), "a") as f:
            f.write("# tampered\n")
        code, out = self.run_install()
        self.assertEqual(code, 1)
        self.assertIn("Checksum for lightkey_api.py does not match", out)
        self.assertFalse(os.path.exists(self.app("lightkey_api.py")))
        self.assertNotIn("curl", self.calls())                                # and it did not fall back to downloading

    def test_a_file_missing_from_the_checksum_list_aborts(self):
        self.sums(skip=("lightkey_api.py",))
        code, out = self.run_install()
        self.assertEqual(code, 1); self.assertFalse(os.path.exists(self.app("lightkey_api.py")))
        self.assertNotIn("curl", self.calls())

    def test_a_tampered_installer_aborts(self):
        with open(os.path.join(self.src, "install.sh"), "a") as f:
            f.write("# tampered\n")
        code, out = self.run_install()
        self.assertEqual(code, 1); self.assertIn("install.sh does not match", out)

    def test_a_tampered_unused_cue_module_aborts_too(self):
        with open(os.path.join(self.src, "lightkey_cues.py"), "a") as f:
            f.write("# tampered\n")
        code, out = self.run_install()
        self.assertEqual(code, 1); self.assertIn("lightkey_cues.py does not match", out)

    def test_cues_flag_needs_the_cue_file_next_to_the_installer(self):
        os.remove(os.path.join(self.src, "lightkey_cues.py")); self.sums(skip=("lightkey_cues.py",))
        code, out = self.run_install("--cues")
        self.assertEqual(code, 1); self.assertIn("lightkey_cues.py is missing", out)

    def test_cues_flag_installs_the_cue_file_offline(self):
        code, out = self.run_install("--cues")
        self.assertEqual(code, 0, out); self.assertTrue(os.path.exists(self.app("lightkey_cues.py")))

    def test_rerun_upgrades_in_place_and_keeps_the_config(self):
        os.makedirs(self.app())
        with open(self.app("config.json"), "w") as f:
            json.dump({"port": 9999, "allow_interfaces": ["en0"]}, f)
        with open(self.app("lightkey_api.py"), "w") as f:
            f.write("old")
        code, out = self.run_install()
        self.assertEqual(code, 0, out)
        self.assertEqual(self.config(), {"port": 9999, "allow_interfaces": ["en0"]})
        self.assertEqual(sha(self.app("lightkey_api.py")), sha(os.path.join(self.src, "lightkey_api.py")))

    def test_without_a_checksum_file_a_clone_still_installs(self):
        os.remove(os.path.join(self.src, "SHA256SUMS"))
        code, out = self.run_install()
        self.assertEqual(code, 0, out); self.assertNotIn("releases/latest", self.calls())

    def test_piped_install_still_downloads_the_latest_release_and_checks_it(self):
        # run as `sh -s` from stdin (like curl | sh) in an empty folder: it must fetch SHA256SUMS first
        empty = os.path.join(self.tmp.name, "empty"); os.makedirs(empty)
        env = dict(os.environ, HOME=self.home, LOG=self.log, IFACE=self.iface, LIGHTKEY_UNAME="Darwin", LIGHTKEY_PY=sys.executable,
                   PATH=self.stubs + os.pathsep + os.environ["PATH"], TMPDIR=self.tmp.name)
        with open(os.path.join(ROOT, "install.sh")) as f:
            script = f.read()
        r = subprocess.run(["sh", "-s"], input=script, cwd=empty, env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 1)                                      # the stub curl cannot fetch
        self.assertIn("Could not download the checksum file", r.stdout)
        self.assertIn("releases/latest/download/SHA256SUMS", self.calls())


if __name__ == "__main__":
    unittest.main()
