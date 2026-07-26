"""验证事件 broker 的快照、增量、溢出和关闭 wire 契约；以真实异步订阅夹具驱动 events 层并约束运行时消费者，
不负责 SSE HTTP 序列化、TaskManager 生命周期或持久化。"""

import unittest

from backend.agent.task.events import TaskEventBroker


class TaskEventBrokerTests(unittest.IsolatedAsyncioTestCase):
    async def test_subscription_starts_with_snapshot_then_receives_deltas(self):
        broker = TaskEventBroker(queue_size=4, stream_id="stream-test")
        subscription = broker.subscribe(
            tasks=[{"task_name": "queued"}],
            running_count=0,
            queued_count=1,
        )

        snapshot = await subscription.next_event()
        delta = broker.publish(
            "task.started",
            {"task": {"task_name": "queued"}},
            running_count=1,
            queued_count=0,
        )
        received = await subscription.next_event()

        self.assertEqual(snapshot["event"], "task.snapshot")
        self.assertEqual(
            set(snapshot),
            {
                "schema_version",
                "stream_id",
                "revision",
                "timestamp",
                "event",
                "running_count",
                "queued_count",
                "payload",
            },
        )
        self.assertEqual(snapshot["schema_version"], 1)
        self.assertRegex(snapshot["timestamp"], r"Z$")
        self.assertEqual(snapshot["stream_id"], "stream-test")
        self.assertEqual(snapshot["revision"], 0)
        self.assertEqual(snapshot["payload"]["tasks"][0]["task_name"], "queued")
        self.assertEqual(received, delta)
        self.assertEqual(set(received["payload"]), {"task"})
        self.assertEqual(received["revision"], 1)
        self.assertEqual(received["running_count"], 1)

    async def test_slow_subscriber_is_closed_instead_of_losing_order(self):
        broker = TaskEventBroker(queue_size=1, stream_id="stream-test")
        subscription = broker.subscribe(
            tasks=[],
            running_count=0,
            queued_count=0,
        )

        broker.publish(
            "task.created",
            {"task": {"task_name": "one"}},
            running_count=0,
            queued_count=1,
        )

        with self.assertRaises(StopAsyncIteration):
            await subscription.next_event()

    async def test_close_ends_every_subscription(self):
        broker = TaskEventBroker(queue_size=2)
        subscription = broker.subscribe(
            tasks=[],
            running_count=0,
            queued_count=0,
        )
        await subscription.next_event()

        broker.close()

        with self.assertRaises(StopAsyncIteration):
            await subscription.next_event()

    async def test_close_preserves_already_queued_runtime_closing_event(self):
        broker = TaskEventBroker(queue_size=1, stream_id="stream-test")
        subscription = broker.subscribe(
            tasks=[],
            running_count=0,
            queued_count=0,
        )
        await subscription.next_event()
        broker.publish(
            "runtime.closing",
            {},
            running_count=0,
            queued_count=0,
        )

        broker.close()

        event = await subscription.next_event()
        self.assertEqual(event["event"], "runtime.closing")
        with self.assertRaises(StopAsyncIteration):
            await subscription.next_event()


if __name__ == "__main__":
    unittest.main()
