import importlib.util, os, socket, struct, time, unittest

spec = importlib.util.spec_from_file_location("lightkey_api", os.path.join(os.path.dirname(__file__), "..", "lightkey_api.py"))
lk = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lk)
spec2 = importlib.util.spec_from_file_location("test_fixtures", os.path.join(os.path.dirname(__file__), "test_fixtures.py"))
tf = importlib.util.module_from_spec(spec2)
spec2.loader.exec_module(tf)


def msgs(pkt):
    top = lk.osc_parse(pkt)[0]
    assert top[0] == "bundle"
    return top[1], [(m[1], m[2]) for m in top[2]]


class CodecTests(unittest.TestCase):
    def test_known_bytes_match_the_osc_spec(self):
        self.assertEqual(lk.osc_message("/a", [1]), b"/a\0\0,i\0\0\0\0\0\x01")
        self.assertEqual(lk.osc_message("/ab", ["hi"]), b"/ab\0,s\0\0hi\0\0")
        self.assertEqual(lk.osc_message("/x", [1.0]), b"/x\0\0,f\0\0\x3f\x80\0\0")
        self.assertEqual(lk.osc_message("/abc", ["abcd"]), b"/abc\0\0\0\0,s\0\0abcd\0\0\0\0")   # 4-char string gets 4 NULs

    def test_roundtrip_types(self):
        args = [3, -7, 0.25, "hello", "abcd", True, False, b"\x01\x02\x03", ("d", 1.5)]
        _, addr, got = lk.osc_parse(lk.osc_message("/fixture/MH 1/overrides/panAngle", args))[0]
        self.assertEqual(addr, "/fixture/MH 1/overrides/panAngle")
        self.assertEqual(got, [3, -7, 0.25, "hello", "abcd", True, False, b"\x01\x02\x03", 1.5])

    def test_bundle_time_tag_precision(self):
        t = 1792650000.123456
        pkt = lk.osc_bundle(t, [lk.osc_message("/a", [1])])
        t2, m = msgs(pkt)
        self.assertLess(abs(t2 - t), 1e-6)
        self.assertEqual(m, [("/a", [1])])

    def test_osc_message_is_four_byte_aligned(self):
        for n in range(0, 9):
            self.assertEqual(len(lk.osc_message("/" + "a" * n, ["x" * n, b"y" * n])) % 4, 0)

    def test_python_osc_style_receiver_reads_our_floats(self):
        # float32 on the wire: 0.5 and 1.0 are exact, 0.502 rounds only at the 7th digit
        _, _, a = lk.osc_parse(lk.osc_message("/c", [0.502]))[0]
        self.assertAlmostEqual(a[0], 0.502, places=6)


class RelayTests(unittest.TestCase):
    def setUp(self):
        lk.CFG.clear(); lk.CFG.update(lk.DEFAULTS)
        lk.CFG.update(relay_port=0, relay_ttl_s=0.6, relay_hello_s=0.3, relay_keyframe_s=0.5)
        fx = lk.Fixtures(tf.FMAP)
        lk.FIXTURES[0] = (fx, 123.0)
        lk.TRACKER[0] = lk.LevelTracker(fx, 100)
        lk.TRACKER[0].frame(1, tf.frame(), 1000)
        self.relay = lk.Relay()
        lk.RELAY = self.relay
        self.relay.start(ips=["127.0.0.1"], port=0)
        self.addr = self.relay.socks[0].getsockname()
        self.cli = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.cli.bind(("127.0.0.1", 0)); self.cli.settimeout(1.0)

    def tearDown(self):
        self.relay.stop()
        self.cli.close()
        lk.FIXTURES[0] = None; lk.TRACKER[0] = None
        lk.RELAY = lk.Relay()

    def recv(self, kind=None, timeout=2.0):
        end = time.time() + timeout
        while time.time() < end:
            try:
                pkt, _ = self.cli.recvfrom(2000)
            except socket.timeout:
                continue
            t, m = msgs(pkt)
            if m[0][0] == "/relay/seq" and (kind is None or m[0][1][1] == kind):
                return t, m
        self.fail("no %s packet" % kind)

    def subscribe(self):
        self.cli.sendto(lk.osc_message("/relay/subscribe", []), self.addr)
        self.recv("hello")

    def test_subscribe_gets_hello_then_keyframe_with_every_fixture(self):
        self.subscribe()
        t, m = self.recv("keyframe")
        names = {a.split("/")[2] for a, _ in m[1:]}
        self.assertTrue({"P1", "MH1"} <= names)   # P25 is on universe 2, which this test never feeds
        self.assertIn("/fixture/P1/overrides/color", [a for a, _ in m[1:]])

    def test_event_carries_dmx_time_and_only_changed_commands(self):
        self.subscribe(); self.recv("keyframe")
        with lk.cv:
            lk.TRACKER[0].frame(1, tf.frame(s10=255), 1792650000250)
        t, m = self.recv("event")
        self.assertAlmostEqual(t, 1792650000.250, places=3)
        self.assertEqual(m[1], ("/fixture/P1/overrides/color", [1.0, 0.0, 0.0]))
        seqs = [m[0][1][0]]
        with lk.cv:
            lk.TRACKER[0].frame(1, tf.frame(s10=255, s11=255), 1792650000300)
        t, m = self.recv("event")
        self.assertEqual(m[1], ("/fixture/P1/overrides/color", [1.0, 1.0, 0.0]))
        self.assertGreater(m[0][1][0], seqs[0])

    def test_sequence_numbers_are_contiguous_per_subscriber(self):
        self.subscribe()
        seen = []
        for i in range(5):
            with lk.cv:
                lk.TRACKER[0].frame(1, tf.frame(s10=i * 40 + 10), 1000 + i * 25)
        end = time.time() + 1.5
        while time.time() < end:
            try:
                pkt, _ = self.cli.recvfrom(2000)
            except socket.timeout:
                break
            seen.append(msgs(pkt)[1][0][1][0])
        self.assertEqual(seen, list(range(seen[0], seen[0] + len(seen))))

    def test_large_event_is_split_in_parts_under_mtu(self):
        lk.CFG["relay_mtu"] = 200
        self.subscribe(); self.recv("keyframe")
        with lk.cv:
            lk.TRACKER[0].frame(1, tf.frame(s10=200, s11=100, s12=50, s104=255, s105=255, s106=10), 2000)
        got = []
        end = time.time() + 1.5
        while time.time() < end:
            try:
                pkt, _ = self.cli.recvfrom(2000)
            except socket.timeout:
                break
            self.assertLessEqual(len(pkt), 260)
            t, m = msgs(pkt)
            if m[0][1][1] == "event":
                got.append(m[0][1][2:4])
        self.assertTrue(got)
        self.assertEqual([g[0] for g in got], list(range(1, got[0][1] + 1)))

    def test_ping_pong_returns_t1_and_two_server_times(self):
        t1 = time.time()
        self.cli.sendto(lk.osc_message("/relay/ping", [("d", t1)]), self.addr)
        pkt, _ = self.cli.recvfrom(2000)
        _, addr, a = lk.osc_parse(pkt)[0]
        self.assertEqual(addr, "/relay/pong")
        self.assertEqual(a[0], t1)
        self.assertLessEqual(a[1], a[2])

    def test_unsubscribed_and_expired_clients_get_nothing(self):
        with lk.cv:
            lk.TRACKER[0].frame(1, tf.frame(s10=255), 3000)
        self.cli.settimeout(0.4)
        with self.assertRaises(socket.timeout):
            self.cli.recvfrom(2000)
        self.subscribe()
        time.sleep(1.6)                                 # ttl 0.6 s, no renewal
        self.assertEqual(self.relay.status()["subscribers"], [])

    def test_renewal_keeps_the_subscription(self):
        self.subscribe()
        for _ in range(4):
            time.sleep(0.3)
            self.cli.sendto(lk.osc_message("/relay/subscribe", []), self.addr)
        self.assertEqual(len(self.relay.status()["subscribers"]), 1)

    def test_subscribe_with_explicit_port(self):
        other = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); other.bind(("127.0.0.1", 0)); other.settimeout(2.0)
        self.cli.sendto(lk.osc_message("/relay/subscribe", [other.getsockname()[1]]), self.addr)
        pkt, _ = other.recvfrom(2000)
        self.assertEqual(msgs(pkt)[1][1][0], "/relay/hello")
        other.close()

    def test_keyframe_on_request(self):
        self.subscribe(); self.recv("keyframe")
        self.cli.sendto(lk.osc_message("/relay/keyframe", []), self.addr)
        self.recv("keyframe")

    def test_subscriber_limit(self):
        lk.CFG["relay_max_subscribers"] = 1
        self.subscribe()
        other = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); other.bind(("127.0.0.1", 0)); other.settimeout(0.5)
        self.cli.sendto(lk.osc_message("/relay/subscribe", [other.getsockname()[1]]), self.addr)
        with self.assertRaises(socket.timeout):
            other.recvfrom(2000)
        other.close()

    def test_full_queue_drops_instead_of_blocking(self):
        self.subscribe()
        r = lk.Relay(); r.socks = [object()]; r.subs = {("x", 1): {}}
        import queue
        r.q = queue.Queue(maxsize=1)
        r.push(1, [{"a": "/a", "v": [1]}]); r.push(2, [{"a": "/a", "v": [1]}])
        self.assertEqual(r.dropped, 1)


if __name__ == "__main__":
    unittest.main()
