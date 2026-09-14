"""In-memory ownership, admission and lifecycle for renewable question jobs."""
from __future__ import annotations

import asyncio
import time
import uuid
from collections import deque
from copy import deepcopy
from dataclasses import dataclass, field, replace
from typing import Any, Awaitable, Callable

from ..config import Settings, parse_csv_ints
from .job_control import JobControl, JobStopped, TERMINAL
from .job_operations import OperationRunner


class JobCapacityError(RuntimeError):
    pass


class UserFacingJobError(RuntimeError):
    """Expected job failure whose short message is safe to show in status UI."""


@dataclass(frozen=True)
class JobSubmission:
    question: str
    quoted: str | None
    owner_id: int
    chat_id: int
    topic_id: int | None
    request_message_id: int
    status_message_id: int
    history: list[dict]
    prepare_request: Callable[[OperationRunner], Awaitable[Any]] | None = None


@dataclass(frozen=True)
class JobSnapshot:
    job_id: str
    owner_id: int
    chat_id: int
    topic_id: int | None
    request_message_id: int
    status_message_id: int
    state: str
    generation: int
    renewals: int
    active_operations: int
    created_at: float
    active_duration: float
    result_ready: bool
    error: str | None


@dataclass
class JobRecord:
    job_id: str
    submission: JobSubmission
    control: JobControl
    created_at: float
    task: asyncio.Task | None = None
    operations: OperationRunner | None = None
    prepared_request: Any = None
    result: Any = None
    error: str | None = None
    delivered: bool = False
    events: list[Any] = field(default_factory=list)


ExecuteJob = Callable[[JobRecord, OperationRunner], Awaitable[Any]]
DeliverJob = Callable[[JobRecord, Any], Awaitable[None]]
EventSink = Callable[[JobRecord, Any], None]


class JobManager:
    """Own all job tasks; no Telegram types or network I/O live in this module."""

    def __init__(
        self,
        settings: Settings,
        execute: ExecuteJob,
        deliver: DeliverJob | None = None,
        *,
        emit: EventSink | None = None,
        clock: Callable[[], float] = time.monotonic,
        delivery_shutdown_grace_sec: float = 5.0,
    ) -> None:
        self.settings = settings
        self.execute = execute
        self.deliver = deliver
        self.emit = emit
        self.clock = clock
        self.delivery_shutdown_grace_sec = max(0.0, delivery_shutdown_grace_sec)
        self._slots = asyncio.Semaphore(settings.question_max_inflight_operations)
        self._jobs: dict[str, JobRecord] = {}
        self._by_message: dict[tuple[int, int], str] = {}
        self._terminal_order: deque[str] = deque()
        self._lock = asyncio.Lock()
        self._monitor: asyncio.Task | None = None
        self._accepting = True
        self._admins = set(parse_csv_ints(settings.admin_ids))

    @property
    def owned_task_count(self) -> int:
        count = sum(
            1
            for record in self._jobs.values()
            if record.task is not None and not record.task.done()
        )
        if self._monitor is not None and not self._monitor.done():
            count += 1
        return count

    def get(self, job_id: str) -> JobRecord | None:
        return self._jobs.get(job_id)

    def snapshot(self, job_id: str) -> JobSnapshot | None:
        record = self._jobs.get(job_id)
        if record is None:
            return None
        control = record.control
        return JobSnapshot(
            job_id=record.job_id,
            owner_id=record.submission.owner_id,
            chat_id=record.submission.chat_id,
            topic_id=record.submission.topic_id,
            request_message_id=record.submission.request_message_id,
            status_message_id=record.submission.status_message_id,
            state=control.state,
            generation=control.generation,
            renewals=control.renewals,
            active_operations=control.active_operations,
            created_at=record.created_at,
            active_duration=control.active_duration,
            result_ready=record.result is not None or record.delivered,
            error=record.error,
        )

    def _emit(self, record: JobRecord, event: Any) -> None:
        record.events.append(event)
        if self.emit is None:
            return
        try:
            self.emit(record, event)
        except Exception:
            return

    async def submit(self, submission: JobSubmission) -> str:
        key = (submission.chat_id, submission.request_message_id)
        async with self._lock:
            existing = self._by_message.get(key)
            if existing is not None:
                return existing
            if not self._accepting:
                raise JobCapacityError("job manager is shutting down")
            active = [
                record
                for record in self._jobs.values()
                if record.control.state not in TERMINAL
            ]
            if len(active) >= self.settings.question_max_pending_jobs:
                raise JobCapacityError("too many pending jobs")
            owner_active = sum(
                record.submission.owner_id == submission.owner_id for record in active
            )
            if owner_active >= self.settings.question_max_jobs_per_user:
                raise JobCapacityError("too many jobs for this user")

            copied = replace(submission, history=deepcopy(submission.history))
            job_id = uuid.uuid4().hex[:16]
            control = JobControl(
                renewal_sec=self.settings.question_renewal_interval_sec,
                clock=self.clock,
            )
            record = JobRecord(job_id, copied, control, self.clock())
            operations = OperationRunner(
                control,
                self._slots,
                lambda event, rec=record: self._emit(rec, event),
                ai_timeout=self.settings.ai_attempt_total_timeout_sec,
                tool_timeout=self.settings.tool_call_total_timeout_sec,
            )
            record.operations = operations
            self._jobs[job_id] = record
            self._by_message[key] = job_id
            record.task = asyncio.create_task(
                self._run(record),
                name=f"question-job:{job_id}",
            )
            self._emit(record, ("state", "QUEUED"))
            if self._monitor is None or self._monitor.done():
                self._monitor = asyncio.create_task(
                    self._monitor_loop(),
                    name="question-job-monitor",
                )
            return job_id

    async def _cancel_record(self, record: JobRecord) -> None:
        if record.control.state not in TERMINAL and record.control.state != "DELIVERING":
            await record.control.stop()
        if record.control.state != "CANCELLED" and record.control.state != "DELIVERING":
            await record.control.finish("CANCELLED")
        if record.control.state == "CANCELLED":
            self._emit(record, ("state", "CANCELLED"))

    async def _run(self, record: JobRecord) -> None:
        try:
            prepare = record.submission.prepare_request
            if prepare is not None:
                record.prepared_request = await prepare(record.operations)
                record.submission = replace(record.submission, prepare_request=None)
            result = await self.execute(record, record.operations)
            record.result = result
            if not await record.control.begin_delivery():
                return
            self._emit(record, ("state", "DELIVERING"))
            if self.deliver is not None:
                await self.deliver(record, result)
            record.delivered = True
            await record.control.finish("COMPLETED")
            self._emit(record, ("state", "COMPLETED"))
        except JobStopped:
            await self._cancel_record(record)
        except asyncio.CancelledError:
            if record.control.state == "DELIVERING":
                record.error = "Tác vụ bị gián đoạn khi bot dừng."
                await record.control.finish("FAILED")
                self._emit(record, ("state", "FAILED"))
            else:
                await self._cancel_record(record)
        except UserFacingJobError as exc:
            record.error = str(exc)[:500]
            if record.control.state not in TERMINAL:
                await record.control.finish("FAILED")
            self._emit(record, ("state", "FAILED"))
        except Exception as exc:  # noqa: BLE001 - terminal manager boundary
            record.error = "Có lỗi bất ngờ xảy ra. Bạn thử lại câu hỏi nhé."
            if record.control.state not in TERMINAL:
                await record.control.finish("FAILED")
            self._emit(record, ("state", "FAILED", type(exc).__name__))
        finally:
            record.prepared_request = None
            await self._remember_terminal(record)

    async def _remember_terminal(self, record: JobRecord) -> None:
        async with self._lock:
            if record.control.state not in TERMINAL:
                return
            self._terminal_order.append(record.job_id)
            while len(self._terminal_order) > 256:
                old_id = self._terminal_order.popleft()
                old = self._jobs.pop(old_id, None)
                if old is not None:
                    key = (old.submission.chat_id, old.submission.request_message_id)
                    if self._by_message.get(key) == old_id:
                        self._by_message.pop(key, None)
            if record.delivered:
                record.result = None

    async def wait(self, job_id: str) -> None:
        record = self._jobs.get(job_id)
        if record is None or record.task is None:
            return
        await asyncio.gather(record.task, return_exceptions=True)

    async def set_status_message_id(
        self,
        job_id: str,
        expected_message_id: int,
        new_message_id: int,
    ) -> bool:
        async with self._lock:
            record = self._jobs.get(job_id)
            if record is None or record.submission.status_message_id != expected_message_id:
                return False
            record.submission = replace(
                record.submission,
                status_message_id=new_message_id,
            )
            return True

    def _authorized_target(
        self,
        record: JobRecord,
        actor_id: int,
        chat_id: int,
        status_message_id: int,
        *,
        stop: bool,
    ) -> bool:
        if (
            record.submission.chat_id != chat_id
            or record.submission.status_message_id != status_message_id
        ):
            return False
        if actor_id == record.submission.owner_id:
            return True
        return stop and actor_id in self._admins

    async def renew(
        self,
        job_id: str,
        generation: int,
        actor_id: int,
        chat_id: int,
        status_message_id: int,
    ) -> str:
        record = self._jobs.get(job_id)
        if record is None:
            return "expired"
        if not self._authorized_target(record, actor_id, chat_id, status_message_id, stop=False):
            return "forbidden"
        if await record.control.renew(generation):
            self._emit(record, ("renewal_accepted", record.control.generation))
            return "renewed"
        return "stale"

    async def stop(
        self,
        job_id: str,
        actor_id: int,
        chat_id: int,
        status_message_id: int,
    ) -> str:
        record = self._jobs.get(job_id)
        if record is None:
            return "expired"
        if not self._authorized_target(record, actor_id, chat_id, status_message_id, stop=True):
            return "forbidden"
        if not await record.control.stop():
            return "terminal"
        self._emit(record, ("state", "CANCELLED"))
        if record.task is not None and record.task is not asyncio.current_task():
            record.task.cancel()
            await asyncio.gather(record.task, return_exceptions=True)
        return "stopped"

    async def _monitor_loop(self) -> None:
        try:
            while self._accepting or any(
                record.control.state not in TERMINAL for record in self._jobs.values()
            ):
                for record in tuple(self._jobs.values()):
                    if record.control.state == "RUNNING":
                        expired = await record.control.expire_if_due()
                        if expired:
                            self._emit(
                                record,
                                ("renewal_requested", record.control.generation),
                            )
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            raise

    async def shutdown(self) -> None:
        async with self._lock:
            self._accepting = False
            records = tuple(self._jobs.values())

        delivery_tasks: list[asyncio.Task] = []
        cancel_tasks: list[asyncio.Task] = []
        for record in records:
            task = record.task
            if task is None or task.done():
                continue
            if record.control.state == "DELIVERING":
                delivery_tasks.append(task)
                continue
            if record.control.state not in TERMINAL:
                await record.control.stop()
                self._emit(record, ("state", "CANCELLED"))
            task.cancel()
            cancel_tasks.append(task)

        if cancel_tasks:
            await asyncio.gather(*cancel_tasks, return_exceptions=True)

        if delivery_tasks:
            _done, pending = await asyncio.wait(
                delivery_tasks,
                timeout=self.delivery_shutdown_grace_sec,
            )
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)

        if self._monitor is not None and not self._monitor.done():
            self._monitor.cancel()
            await asyncio.gather(self._monitor, return_exceptions=True)
        self._monitor = None
