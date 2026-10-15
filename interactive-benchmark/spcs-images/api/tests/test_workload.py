import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import server


def write_workload(queries) -> str:
    path = Path(tempfile.mkdtemp()) / "workload.json"
    path.write_text(json.dumps({"queries": queries}))
    return str(path)


def entry(**overrides):
    base = {"id": "q1", "sql": "select 1", "weight": 60, "database": "DB", "schema": "S"}
    return {**base, **overrides}


class WorkloadFileTest(unittest.TestCase):
    def test_loads_weights_and_context(self) -> None:
        registry = server.load_workload_file(
            write_workload([entry(), entry(id="q2", weight=40, database="DB2", schema="S2")])
        )

        self.assertEqual(server.Query("q1", "select 1", 60.0, ("DB", "S")), registry["q1"])
        self.assertEqual(("DB2", "S2"), registry["q2"].context)

    def test_rejects_invalid_entries(self) -> None:
        cases = {
            "non-positive weight": [entry(weight=0)],
            "empty SQL": [entry(sql="  ")],
            "Duplicate query id": [entry(), entry()],
            "has no queries": [],
        }
        for message, queries in cases.items():
            with self.subTest(message), self.assertRaisesRegex(ValueError, message):
                server.load_workload_file(write_workload(queries))

    def test_rejects_missing_field(self) -> None:
        broken = entry()
        del broken["schema"]
        with self.assertRaises(KeyError):
            server.load_workload_file(write_workload([broken]))


class ContextPoolTest(unittest.TestCase):
    def test_connections_are_reused_only_within_their_context(self) -> None:
        pool = server.ConnectionPool(size=4)
        created = []

        def new_connection(context):
            conn = Mock(name=f"conn-{len(created)}")
            conn.is_closed.return_value = False
            created.append(context)
            return conn

        pool._new_connection = new_connection
        a = pool.acquire(("DB", "A"))
        pool.release(a, ("DB", "A"))
        b = pool.acquire(("DB", "B"))
        pool.release(b, ("DB", "B"))

        self.assertIs(a, pool.acquire(("DB", "A")))
        self.assertEqual([("DB", "A"), ("DB", "B")], created)

    def test_warmup_splits_connections_by_context_weight(self) -> None:
        pool = server.ConnectionPool(size=10)
        pool._new_connection = Mock(side_effect=lambda context: Mock(context=context))

        pool.warmup(8, {("DB", "A"): 70, ("DB", "B"): 25, ("DB", "C"): 5})
        self.assertEqual([6, 2, 0], [pool._idle[c].qsize() if c in pool._idle else 0
                                     for c in (("DB", "A"), ("DB", "B"), ("DB", "C"))])

    def test_split_by_weight_keeps_the_count(self) -> None:
        for count, weights in ((10, {"a": 95, "b": 5}), (7, {"a": 1, "b": 1, "c": 1}), (1, {"a": 1, "b": 99})):
            with self.subTest(count=count, weights=weights):
                self.assertEqual(count, len(server.split_by_weight(count, weights)))
        self.assertEqual(["b"], server.split_by_weight(1, {"a": 1, "b": 99}))


class WarmupCapacityTest(unittest.TestCase):
    def test_warmed_connections_do_not_consume_capacity(self) -> None:
        pool = server.ConnectionPool(size=2)
        pool._new_connection = Mock(side_effect=lambda context: Mock(**{"is_closed.return_value": False}))
        pool.warmup(2)
        with patch.object(server, "POOL_ACQUIRE_TIMEOUT", 0.1):
            first = pool.acquire(server.DEFAULT_CONTEXT)
            second = pool.acquire(server.DEFAULT_CONTEXT)
        self.assertIsNot(first, second)
        self.assertEqual(2, pool._new_connection.call_count)


class WorkloadEndpointTest(unittest.IsolatedAsyncioTestCase):
    async def test_workload_lists_ids_and_weights(self) -> None:
        registry = {
            "b": server.Query("b", "select 2", 40.0, ("DB", "S")),
            "a": server.Query("a", "select 1", 60.0, ("DB", "S")),
        }
        with patch.object(server, "query_registry", registry):
            self.assertEqual(
                [{"id": "a", "weight": 60.0}, {"id": "b", "weight": 40.0}],
                await server.workload(),
            )


if __name__ == "__main__":
    unittest.main()
