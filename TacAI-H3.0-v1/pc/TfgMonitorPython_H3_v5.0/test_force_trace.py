"""Force diagnostics preserve evidence without modifying or filtering force."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import numpy as np

from force_trace import ForceTrace, SCHEMA, VERSION
import force_trace


class ForceTraceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "traces"
        self.env = mock.patch.dict(os.environ)
        self.env.start()
        self.addCleanup(self.env.stop)
        os.environ.pop("TACAI_FORCE_TRACE_DIR", None)

    def test_bounded_by_count_and_actual_received_time_with_deep_copy(self):
        count_only = ForceTrace(self.root, max_frames=3, max_span_s=100)
        for i in range(4):
            count_only.append(dict(received_at=0., force_n=i))
        self.assertEqual([s["force_n"] for s in count_only.frames], [1, 2, 3])
        trace = ForceTrace(self.root, max_frames=3, max_span_s=2)
        raw = np.arange(24, dtype=float)
        sample = dict(received_at=0., raw=raw, nested={"state": ["original"]})
        trace.append(sample)
        raw[:] = 900
        sample["nested"]["state"][0] = "changed"
        self.assertEqual(trace.frames[0]["raw"][0], 0.)
        self.assertEqual(trace.frames[0]["nested"]["state"], ["original"])
        for t in (1., 2., 2.5):
            trace.append(dict(received_at=t, force_n=t))
        self.assertEqual([s["received_at"] for s in trace.frames], [1., 2., 2.5])
        trace.append(dict(received_at=5., force_n=5.))
        self.assertEqual([s["received_at"] for s in trace.frames], [5.])
        trace.append(dict(received_at=1., force_n=1.))
        self.assertEqual([s["received_at"] for s in trace.frames], [5.])

    def test_zero_and_residual_forces_are_retained_without_recalculation(self):
        trace = ForceTrace(self.root)
        for i, value in enumerate([0., .28, 25., 0.]):
            trace.append(dict(received_at=float(i), force_n=value,
                              raw=[i] * 24, tare_raw=[0] * 24))
        path = trace.save("manual_unloaded_tare", {"host_tare_epoch": 2})
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual([r["force_n"] for r in rows[1:]], [0., .28, 25., 0.])
        self.assertEqual(rows[2]["raw"], [1] * 24)
        self.assertEqual(len(trace), 4)  # A successful save does not clear evidence.

    def test_monotonic_stream_prunes_without_rescanning_the_buffer(self):
        trace = ForceTrace(self.root, max_span_s=2)
        with mock.patch("force_trace._sample_time", wraps=force_trace._sample_time) as clock:
            for i in range(1000):
                trace.append(dict(received_at=i * .01, force_n=0.))
        self.assertLess(clock.call_count, 4000)
        self.assertLessEqual(trace.frames[-1]["received_at"] - trace.frames[0]["received_at"], 2.)

    def test_out_of_order_and_untimed_samples_preserve_evidence_and_bounds(self):
        trace = ForceTrace(self.root, max_frames=5, max_span_s=2)
        for t in (None, 5., 4., 6., 7.):
            trace.append(dict(received_at=t, force_n=t))
        self.assertEqual([s["received_at"] for s in trace.frames], [None, 5., 6., 7.])
        trace.append(dict(received_at=8., force_n=8.))
        self.assertEqual([s["received_at"] for s in trace.frames], [None, 6., 7., 8.])
        self.assertTrue(trace._time_ordered)

    def test_jsonl_header_unicode_and_nonfinite_are_portable_json(self):
        trace = ForceTrace(self.root)
        trace.append(dict(received_at=1., force_n=float("nan"),
                          raw=np.array([float("inf"), -float("inf"), 3.]),
                          nested=[np.float32("nan"), {"value": np.float64("inf")}]))
        trace.append(dict(received_at=2.5, force_n=0.))
        path = trace.save("卸载前后", {"positive_inf": float("inf"), "raw_source": "原始"})
        rows = [json.loads(line, parse_constant=lambda value: self.fail(value))
                for line in path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(rows[0]["schema"], SCHEMA)
        self.assertEqual(rows[0]["version"], VERSION)
        self.assertEqual(rows[0]["reason"], "卸载前后")
        self.assertEqual(rows[0]["metadata"], {"positive_inf": None, "raw_source": "原始"})
        self.assertEqual(rows[0]["count"], 2)
        self.assertEqual(rows[0]["span_s"], 1.5)
        self.assertIsNone(rows[1]["force_n"])
        self.assertEqual(rows[1]["raw"], [None, None, 3.])
        self.assertEqual(rows[1]["nested"], [None, {"value": None}])
        second = trace.save("again", {})
        self.assertNotEqual(path, second)
        self.assertEqual(len(list(self.root.glob("*.jsonl"))), 2)
        self.assertFalse(list(self.root.glob("*.tmp")))

    def test_failed_atomic_save_preserves_buffer_and_removes_temporary(self):
        trace = ForceTrace(self.root)
        trace.append(dict(received_at=1., force_n=.5))
        with mock.patch("force_trace.os.replace", side_effect=OSError("storage unavailable")):
            self.assertIsNone(trace.save("residual", {}))
        self.assertEqual(len(trace), 1)
        self.assertEqual(trace.frames[0]["force_n"], .5)
        self.assertFalse(list(self.root.iterdir()))
        self.assertIsNotNone(trace.save("retry", {}))

    def test_unwritable_root_preserves_buffer(self):
        self.root.write_text("a file cannot be a directory", encoding="utf-8")
        trace = ForceTrace(self.root)
        trace.append(dict(received_at=1., force_n=0.))
        self.assertIsNone(trace.save("unavailable", {}))
        self.assertEqual(len(trace), 1)

    def test_empty_trace_does_not_create_directory_and_clear_resets_time(self):
        trace = ForceTrace(self.root, max_span_s=1)
        self.assertIsNone(trace.save("empty", {}))
        self.assertFalse(self.root.exists())
        trace.append(dict(received_at=100., force_n=2.))
        trace.clear()
        self.assertEqual(len(trace), 0)
        trace.append(dict(received_at=1., force_n=0.))
        self.assertEqual(len(trace), 1)

    def test_environment_and_frozen_paths(self):
        override = Path(self.temp.name) / "override"
        with mock.patch.dict(os.environ, TACAI_FORCE_TRACE_DIR=str(override)):
            self.assertEqual(ForceTrace(self.root).root, override)
        with mock.patch("force_trace.sys.frozen", True, create=True), mock.patch(
                "force_trace.sys.executable", str(Path(self.temp.name) / "app.exe")):
            self.assertEqual(ForceTrace().root, Path(self.temp.name) / "logs" / "force_trace")


if __name__ == "__main__":
    unittest.main()
