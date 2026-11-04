import asyncio
import unittest
from unittest.mock import Mock, patch

from fastapi import HTTPException

import server


class ConnectionPoolWarmupTest(unittest.TestCase):
    def test_warmup_raises_connection_error(self) -> None:
        pool = server.ConnectionPool(size=2)
        pool._new_connection = Mock(side_effect=RuntimeError("warehouse access denied"))

        with self.assertRaisesRegex(
            RuntimeError,
            "Pool warmup failed after 0/2: warehouse access denied",
        ):
            pool.warmup()


class ReadinessTest(unittest.IsolatedAsyncioTestCase):
    def tearDown(self) -> None:
        server.pool_warmup_error = None
        server.pool_ready.clear()

    async def test_readiness_rejects_failed_warmup(self) -> None:
        server.pool_warmup_error = "warehouse access denied"

        with self.assertRaises(HTTPException) as raised:
            await server.ready()

        self.assertEqual(503, raised.exception.status_code)
        self.assertEqual(
            "Connection pool warmup failed; inspect API logs",
            raised.exception.detail,
        )

    async def test_readiness_accepts_successful_warmup(self) -> None:
        server.pool_ready.set()

        self.assertEqual({"status": "ready"}, await server.ready())


class ExecutorTest(unittest.IsolatedAsyncioTestCase):
    def tearDown(self) -> None:
        server.pool_ready.clear()

    async def test_query_executor_matches_pool_size(self) -> None:
        with patch.object(server, "POOL_WARMUP", 0), patch.object(server.pool, "close_all"):
            async with server.lifespan(server.app):
                executor = asyncio.get_running_loop()._default_executor
                self.assertEqual(server.POOL_SIZE, executor._max_workers)


if __name__ == "__main__":
    unittest.main()
