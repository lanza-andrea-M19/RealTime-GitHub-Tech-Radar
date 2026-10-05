import asyncio
import json
import logging
from abc import ABC, abstractmethod
from typing import AsyncGenerator, Any
from src.common.config import settings
from src.streaming.models import IncomingGitHubEvent

logger = logging.getLogger(__name__)


class StreamBroker(ABC):
    """Abstract interface for event streaming brokers (Kafka/Redpanda or in-memory)."""

    @abstractmethod
    async def start(self) -> None:
        """Initializes producer/consumer connections."""
        pass

    @abstractmethod
    async def stop(self) -> None:
        """Closes connections cleanly."""
        pass

    @abstractmethod
    async def publish_event(self, event: IncomingGitHubEvent) -> None:
        """Publishes an incoming GitHub event to the events topic/queue."""
        pass

    @abstractmethod
    async def consume_events(self) -> AsyncGenerator[IncomingGitHubEvent, None]:
        """Continuously streams incoming GitHub events."""
        pass


class InMemoryBroker(StreamBroker):
    """Lightweight in-memory asynchronous queue broker for development and testing."""

    def __init__(self, maxsize: int = 1000):
        self._queue: asyncio.Queue[IncomingGitHubEvent] = asyncio.Queue(maxsize=maxsize)
        self._running = False

    async def start(self) -> None:
        self._running = True
        logger.info("InMemoryBroker started.")

    async def stop(self) -> None:
        self._running = False
        logger.info("InMemoryBroker stopped.")

    async def publish_event(self, event: IncomingGitHubEvent) -> None:
        await self._queue.put(event)
        logger.debug(f"Event #{event.issue_number} on {event.repo} enqueued in-memory.")

    async def consume_events(self) -> AsyncGenerator[IncomingGitHubEvent, None]:
        while self._running:
            try:
                # Wait for next event with a timeout to allow checking self._running
                event = await asyncio.wait_for(self._queue.get(), timeout=1.0)
                yield event
                self._queue.task_done()
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break


class RedpandaBroker(StreamBroker):
    """Kafka-compatible broker connecting to Redpanda via aiokafka."""

    def __init__(
        self,
        bootstrap_servers: str | None = None,
        topic: str | None = None,
        group_id: str = "techradar-stream-processor",
    ):
        self.bootstrap_servers = bootstrap_servers or settings.kafka_bootstrap_servers
        self.topic = topic or settings.kafka_topic_events
        self.group_id = group_id
        self._producer = None
        self._consumer = None
        self._running = False

    async def start(self) -> None:
        from aiokafka import AIOKafkaProducer, AIOKafkaConsumer

        self._producer = AIOKafkaProducer(
            bootstrap_servers=self.bootstrap_servers,
            value_serializer=lambda v: json.dumps(v).encode("utf-8"),
        )
        await self._producer.start()

        self._consumer = AIOKafkaConsumer(
            self.topic,
            bootstrap_servers=self.bootstrap_servers,
            group_id=self.group_id,
            auto_offset_reset="latest",
            value_deserializer=lambda v: json.loads(v.decode("utf-8")),
        )
        await self._consumer.start()
        self._running = True
        logger.info(f"RedpandaBroker connected to {self.bootstrap_servers} on topic '{self.topic}'.")

    async def stop(self) -> None:
        self._running = False
        if self._producer:
            await self._producer.stop()
        if self._consumer:
            await self._consumer.stop()
        logger.info("RedpandaBroker stopped.")

    async def publish_event(self, event: IncomingGitHubEvent) -> None:
        if not self._producer:
            raise RuntimeError("RedpandaBroker producer is not started.")
        await self._producer.send_and_wait(self.topic, event.model_dump())

    async def consume_events(self) -> AsyncGenerator[IncomingGitHubEvent, None]:
        if not self._consumer:
            raise RuntimeError("RedpandaBroker consumer is not started.")
        while self._running:
            try:
                msg = await self._consumer.getone()
                yield IncomingGitHubEvent(**msg.value)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error consuming from Redpanda: {e}")
                await asyncio.sleep(1.0)


async def get_stream_broker(force_in_memory: bool = False) -> StreamBroker:
    """Factory creating a broker: tests Redpanda, falls back to InMemoryBroker if offline."""
    if force_in_memory:
        return InMemoryBroker()

    # Try quick probe to Redpanda
    try:
        from aiokafka import AIOKafkaProducer
        probe_producer = AIOKafkaProducer(
            bootstrap_servers=settings.kafka_bootstrap_servers,
            request_timeout_ms=1000,
        )
        await asyncio.wait_for(probe_producer.start(), timeout=1.5)
        await probe_producer.stop()
        logger.info("Redpanda broker is available. Using RedpandaBroker.")
        return RedpandaBroker()
    except Exception:
        logger.info(
            f"Redpanda unreachable at {settings.kafka_bootstrap_servers}. Using InMemoryBroker fallback."
        )
        return InMemoryBroker()
