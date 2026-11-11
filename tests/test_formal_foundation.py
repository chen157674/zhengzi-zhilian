import os
from pathlib import Path
import socket
import unittest

from fitness_app import create_app
from fitness_app.paths import ROOT
from fitness_app.runtime import launch, read_instance, stop_instance, health
from fitness_app.upstream import calculate_angle, load_upstream_definition


class FoundationTests(unittest.TestCase):
    def test_source_reuse_and_geometry(self):
        self.assertEqual(load_upstream_definition("squat")["name"], "squat")
        self.assertAlmostEqual(calculate_angle((0, 1), (0, 0), (1, 0)), 90)
        with self.assertRaises(ValueError):
            calculate_angle((0, 0), (0, 0), (1, 0))
        with self.assertRaises(ValueError):
            load_upstream_definition("../other")

    def test_model_missing_and_host_origin(self):
        app = create_app({"TESTING": True, "MODEL_PATH": ROOT / ".tmp/missing-test.task"})
        client = app.test_client()
        self.assertFalse(client.get("/api/v1/health").json["data"]["ready"])
        self.assertEqual(client.get("/api/v1/health", base_url="http://untrusted.example").status_code, 400)
        self.assertEqual(client.post("/no-route", headers={"Origin": "https://other.example"}).status_code, 403)

    def test_instance_reuse_foreign_port_and_stop(self):
        previous = os.environ.get("FITNESS_RUNTIME_DIR")
        previous_database = os.environ.get("FITNESS_DATABASE_PATH")
        os.environ["FITNESS_RUNTIME_DIR"] = str(ROOT / ".tmp/tests/foundation")
        os.environ["FITNESS_DATABASE_PATH"] = str(ROOT / ".tmp/tests/foundation/test.sqlite3")
        foreign = socket.socket()
        foreign.bind(("127.0.0.1", 0))
        foreign.listen()
        try:
            first = launch(foreign.getsockname()[1], False)
            second = launch(0, False)
            self.assertEqual(first["pid"], second["pid"])
            self.assertTrue(second["reused"])
            self.assertNotEqual(first["port"], foreign.getsockname()[1])
            self.assertTrue(health(read_instance())["ready"])
            self.assertTrue(stop_instance()["stopped"])
            self.assertIsNone(health({"port":first["port"],"instance_id":"wrong"}))
            self.assertTrue(foreign.fileno() >= 0)
        finally:
            foreign.close()
            record = read_instance()
            if record and health(record):
                stop_instance()
            if previous is None:
                os.environ.pop("FITNESS_RUNTIME_DIR", None)
            else:
                os.environ["FITNESS_RUNTIME_DIR"] = previous
            if previous_database is None:
                os.environ.pop("FITNESS_DATABASE_PATH",None)
            else:
                os.environ["FITNESS_DATABASE_PATH"]=previous_database


if __name__ == "__main__":
    unittest.main()
