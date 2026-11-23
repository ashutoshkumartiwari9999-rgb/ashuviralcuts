import math
import asyncio
import tempfile
import threading
import time
import unittest
import uuid
import wave
from pathlib import Path
from unittest.mock import patch

import numpy as np

from fastapi import HTTPException
from fastapi import FastAPI
import httpx

import highlights
import action
import jobs
import processing


class ProcessingGateTests(unittest.TestCase):
    def tearDown(self):
        owner = processing.active_owner()
        if owner:
            processing.release(owner)

    def test_simultaneous_direct_requests_return_clear_busy_response(self):
        started = threading.Event()
        finish = threading.Event()
        state = {}
        test_app = FastAPI()

        @test_app.post("/process")
        def process_request():
            job = uuid.uuid4().hex
            state[job] = {"status": "running"}
            processing.start(job, lambda: (started.set(), finish.wait(3)), state)
            return {"job": job}

        async def exercise():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=test_app), base_url="http://testserver") as client:
                first = await client.post("/process")
                self.assertEqual(first.status_code, 200)
                self.assertTrue(await asyncio.to_thread(started.wait, 1))
                second = await client.post("/process")
                self.assertEqual(second.status_code, 409)
                self.assertIn("Another video is being processed", second.json()["detail"])
                finish.set()
                deadline = time.monotonic() + 2
                while processing.active_owner() and time.monotonic() < deadline:
                    await asyncio.sleep(.01)
                self.assertIsNone(processing.active_owner())

        try:
            asyncio.run(exercise())
        finally:
            finish.set()

    def test_queue_and_direct_jobs_share_the_same_slot(self):
        root = tempfile.TemporaryDirectory(prefix="ashuviralcuts-queue-gate-")
        state = {}
        started = threading.Event()
        finish = threading.Event()

        def queued_handler(_payload):
            child = uuid.uuid4().hex
            state[child] = {"status": "running"}

            def run_child():
                started.set()
                finish.wait(3)
                state[child] = {"status": "done"}

            processing.start(child, run_child, state)
            return {"job": child}

        queue = jobs.JobQueue(Path(root.name) / "jobs.sqlite3", {"make": queued_handler}, state, autostart=False)
        try:
            queued = queue.enqueue("make", {"url": "https://youtu.be/example"})
            runner = threading.Thread(target=queue.run_once)
            runner.start()
            self.assertTrue(started.wait(1))
            with self.assertRaises(HTTPException) as busy:
                processing.start("direct-conflict", lambda: None, state)
            self.assertEqual(busy.exception.status_code, 409)
            self.assertEqual(queue.get(queued["id"])["status"], "running")
            finish.set()
            runner.join(3)
            self.assertFalse(runner.is_alive())
            self.assertEqual(queue.get(queued["id"])["status"], "done")
            self.assertIsNone(processing.active_owner())
        finally:
            finish.set()
            queue.close()
            root.cleanup()

    def test_slot_releases_after_success_failure_timeout_and_cancellation(self):
        for label, error in (
            ("success", None),
            ("failure", RuntimeError("worker failed")),
            ("timeout", TimeoutError("worker timed out")),
            ("cancel", InterruptedError("worker canceled")),
        ):
            with self.subTest(label=label):
                state = {label: {"status": "running"}}
                done = threading.Event()

                def work():
                    if error:
                        raise error
                    state[label] = {"status": "done"}

                original_thread = threading.Thread

                class NotifyingThread(processing.threading.Thread):
                    def run(self):
                        try:
                            super().run()
                        finally:
                            done.set()

                processing.threading.Thread = NotifyingThread
                try:
                    processing.start(label + uuid.uuid4().hex[:8], work, state)
                finally:
                    processing.threading.Thread = original_thread
                self.assertTrue(done.wait(2))
                self.assertIsNone(processing.active_owner())

    def test_slot_releases_if_thread_cannot_start(self):
        from unittest.mock import patch

        state = {"start-failure": {"status": "running"}}
        with patch("processing.threading.Thread.start", side_effect=RuntimeError("thread start failed")):
            with self.assertRaises(RuntimeError):
                processing.start("start-failure", lambda: None, state)
        self.assertIsNone(processing.active_owner())
        self.assertNotIn("start-failure", state)

    def test_selected_clip_duration_10_30_120_and_early_source_end(self):
        for duration in (10, 30, 120):
            self.assertEqual(processing.clip_length_bounds(duration, duration), (duration, duration))
            fitted = processing.fit_clip_range(10, 80, 240, duration, duration)
            self.assertIsNotNone(fitted)
            self.assertTrue(math.isclose(duration, fitted[1] - fitted[0]))
        self.assertEqual(processing.fit_clip_range(0, 45, 45, 120, 120), (0.0, 45.0))
        self.assertEqual(processing.fit_clip_range(95, 100, 100, 30, 30), (70.0, 100.0))
        self.assertIsNone(processing.fit_clip_range(0, 0, 19))
        self.assertEqual(processing.clip_length_bounds(20, 25), (20, 25))
        for invalid in (9, 121):
            with self.assertRaises(ValueError):
                processing.clip_length_bounds(invalid, invalid)

    def test_reused_motion_skips_redundant_candidate_optical_flow(self):
        frames = np.zeros((12, 90, 160, 3), dtype=np.uint8)
        frames[4:7, :, :, 0] = 255
        frames[7:, :, :, 2] = 255
        completed = type('Completed', (), {'stdout': frames.tobytes()})()
        with patch.object(action.subprocess, 'run', return_value=completed), \
             patch.object(action.cv2, 'calcOpticalFlowFarneback', side_effect=AssertionError('duplicate optical flow')) as flow:
            features = action.analyze('unused-synthetic-input.mp4', 0, 2, include_motion=False)
        flow.assert_not_called()
        self.assertIsNone(features['motion'])
        self.assertIsNone(features['cuts'])
        self.assertIn('flash', features)
        self.assertIn('strobe', features)

    def test_audio_analysis_decodes_in_bounded_chunks(self):
        with tempfile.TemporaryDirectory(prefix="ashuviralcuts-audio-gate-") as folder:
            path = Path(folder) / "tone.wav"
            with wave.open(str(path), "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(8000)
                samples = bytearray()
                for index in range(45 * 8000):
                    value = int(8000 * math.sin(2 * math.pi * 440 * index / 8000))
                    samples.extend(value.to_bytes(2, "little", signed=True))
                output.writeframes(samples)
            rms, high_frequency = highlights._audio(path)
            self.assertEqual(len(rms), 45)
            self.assertEqual(len(high_frequency), 45)
            self.assertGreater(float(rms.mean()), .1)


if __name__ == "__main__":
    unittest.main()
