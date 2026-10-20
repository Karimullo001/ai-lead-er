"""
Comprehensive JARVIS Personal AI Agent Test Suite
Uses standard library unittest and unittest.mock exclusively.
Runs cleanly under Python 3.13 in isolated sandbox environments.
"""

import asyncio
import os
import sys
import time
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

# --- Setup mocks for optional external packages in test environment ---
class _MockRouter:
    def message(self, *args, **kwargs):
        def decorator(f):
            return f
        return decorator
    def callback_query(self, *args, **kwargs):
        def decorator(f):
            return f
        return decorator

class _MockF:
    def __call__(self, *args, **kwargs):
        return _MockF()
    def __getattr__(self, name):
        return _MockF()
    def __invert__(self):
        return _MockF()
    def __and__(self, other):
        return _MockF()
    def __or__(self, other):
        return _MockF()

if "asyncpg" not in sys.modules:
    sys.modules["asyncpg"] = MagicMock()
if "redis" not in sys.modules:
    sys.modules["redis"] = MagicMock()
if "httpx" not in sys.modules:
    sys.modules["httpx"] = MagicMock()
if "pydantic" not in sys.modules:
    mock_pydantic = MagicMock()
    class _MockBaseModel:
        def __init__(self, **kwargs):
            for k, v in kwargs.items():
                setattr(self, k, v)
        @classmethod
        def model_validate(cls, obj):
            return cls()
        @classmethod
        def model_rebuild(cls):
            pass
        def model_dump(self):
            return self.__dict__
    mock_pydantic.BaseModel = _MockBaseModel
    mock_pydantic.Field = lambda *args, **kwargs: None
    sys.modules["pydantic"] = mock_pydantic
if "aiogram" not in sys.modules:
    mock_aiogram = MagicMock()
    mock_aiogram.Router = _MockRouter
    mock_aiogram.F = _MockF()
    sys.modules["aiogram"] = mock_aiogram
    sys.modules["aiogram.filters"] = MagicMock()
    sys.modules["aiogram.types"] = MagicMock()
    sys.modules["aiogram.client.default"] = MagicMock()
    sys.modules["aiogram.enums"] = MagicMock()

# Ensure project root is in sys.path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.calling import CallingConfig, CallingManager, CallState, CallTrigger, get_calling_manager
from core.task_manager import TaskManager, TaskStatus


class TestCallingSubsystem(unittest.TestCase):
    """Test Telegram Voice Calling subsystem, truthful diagnostics, and policies."""

    def setUp(self):
        self.config = CallingConfig(
            enabled=True,
            api_id=123456,
            api_hash="test_api_hash_abc123",
            session_string="test_session_string",
            authorized_user_id=987654321,
            quiet_hours_start=23,
            quiet_hours_end=7,
            cooldown_seconds=300,
            max_retries=2,
        )
        self.cm = CallingManager(self.config)

    def test_default_config_loading(self):
        with patch.dict(os.environ, {
            "TELEGRAM_CALLING_ENABLED": "true",
            "TELEGRAM_CALLING_API_ID": "99999",
            "TELEGRAM_CALLING_API_HASH": "hash999",
            "TELEGRAM_CALLING_SESSION_STRING": "session_xyz",
            "TELEGRAM_CALLING_AUTHORIZED_USER_ID": "111222333",
        }):
            cfg = CallingConfig.from_env()
            self.assertTrue(cfg.enabled)
            self.assertEqual(cfg.api_id, 99999)
            self.assertEqual(cfg.api_hash, "hash999")
            self.assertEqual(cfg.session_string, "session_xyz")
            self.assertEqual(cfg.authorized_user_id, 111222333)

    def test_status_diagnostics_when_library_missing(self):
        self.cm._mtproto_available = False
        self.cm._mtproto_error = "MTProto library not installed."
        status = self.cm.get_status()
        self.assertEqual(status["state"], CallState.LIBRARY_UNAVAILABLE.value)
        self.assertFalse(status["mtproto_library_available"])
        self.assertTrue(status["has_credentials"])
        self.assertIn("MTProto library not installed", status["library_diagnostic"])

    def test_status_diagnostics_when_disabled(self):
        self.cm._mtproto_available = True
        self.cm._voip_bridge_available = True
        self.cm.config.enabled = False
        status = self.cm.get_status()
        self.assertEqual(status["state"], CallState.DISABLED.value)

    def test_is_ready_to_call_when_voip_bridge_missing(self):
        self.cm._mtproto_available = True
        self.cm._voip_bridge_available = False
        ok, reason = self.cm.is_ready_to_call(recipient_id=987654321)
        self.assertFalse(ok)
        self.assertIn("WebRTC VoIP audio", reason)

    def test_is_ready_to_call_unauthorized_user(self):
        self.cm._mtproto_available = True
        self.cm._voip_bridge_available = True
        ok, reason = self.cm.is_ready_to_call(recipient_id=555555)
        self.assertFalse(ok)
        self.assertIn("Calling restricted", reason)

    def test_is_ready_to_call_cooldown(self):
        self.cm._mtproto_available = True
        self.cm._voip_bridge_available = True
        self.cm.last_call_time = time.time()
        ok, reason = self.cm.is_ready_to_call(recipient_id=987654321)
        self.assertFalse(ok)
        self.assertIn("cooldown active", reason.lower())

    def test_enable_and_disable_security(self):
        ok, msg = self.cm.disable(user_id=123)
        self.assertFalse(ok)
        self.assertIn("Permission denied", msg)

        ok, msg = self.cm.disable(user_id=987654321)
        self.assertTrue(ok)
        self.assertFalse(self.cm.config.enabled)

        ok, msg = self.cm.enable(user_id=987654321)
        self.assertTrue(ok)
        self.assertTrue(self.cm.config.enabled)

    def test_dry_run_test_call(self):
        res = self.cm.test_call(recipient_id=987654321)
        self.assertIn("test_passed", res)
        self.assertIn("readiness_reason", res)
        self.assertIn("status_details", res)
        self.assertIn("recommendation", res)

    def test_singleton_get_calling_manager(self):
        cm1 = get_calling_manager()
        cm2 = get_calling_manager()
        self.assertIs(cm1, cm2)


class TestTaskManagerContractsAndRecovery(unittest.IsolatedAsyncioTestCase):
    """Test TaskManager contracts, emergency stop, and failure recovery."""

    async def test_emergency_stop_lifecycle(self):
        tm = TaskManager("postgresql://mock/db")
        mock_conn = AsyncMock()
        mock_pool = MagicMock()
        mock_pool.acquire.return_value.__aenter__.return_value = mock_conn
        tm.pool = mock_pool

        await tm.set_emergency_stop(True, user_id="user_admin", reason="Test Kill Switch")
        self.assertTrue(mock_conn.execute.called)

        mock_conn.fetchrow.return_value = {"value_bool": True}
        active = await tm.is_emergency_stop_active()
        self.assertTrue(active)

        mock_conn.fetchrow.return_value = {"value_bool": False}
        active = await tm.is_emergency_stop_active()
        self.assertFalse(active)

    async def test_mark_notified_delivery_and_retry(self):
        tm = TaskManager("postgresql://mock/db")
        mock_conn = AsyncMock()
        mock_pool = MagicMock()
        mock_pool.acquire.return_value.__aenter__.return_value = mock_conn
        tm.pool = mock_pool

        # Successful delivery
        await tm.mark_notified("task-12345", success=True)
        query = mock_conn.execute.call_args[0][0]
        self.assertIn("UPDATE tasks SET notified_at=$2", query)

        # Failed delivery releases notified_at to NULL for retry
        await tm.mark_notified("task-12345", success=False)
        val = mock_conn.execute.call_args[0][2]
        self.assertIsNone(val)

    async def test_claim_pending_notifications(self):
        tm = TaskManager("postgresql://mock/db")
        mock_conn = AsyncMock()
        mock_pool = MagicMock()
        mock_pool.acquire.return_value.__aenter__.return_value = mock_conn
        tm.pool = mock_pool

        mock_conn.fetch.return_value = [
            {"task_id": "task-abc", "status": "COMPLETED", "chat_id": "123"}
        ]
        claimed = await tm.claim_pending_notifications(batch_size=10)
        self.assertEqual(len(claimed), 1)
        self.assertEqual(claimed[0]["task_id"], "task-abc")

    async def test_recover_orphaned_tasks_category_differentiation(self):
        tm = TaskManager("postgresql://mock/db")
        mock_conn = AsyncMock()
        mock_pool = MagicMock()
        mock_pool.acquire.return_value.__aenter__.return_value = mock_conn
        tm.pool = mock_pool
        mock_queue = AsyncMock()

        # Simulate 1 Category A task and 1 Category C task stuck in RUNNING
        mock_conn.fetch.return_value = [
            {"task_id": "task-cat-a", "decision_category": "A", "retry_count": 0, "max_retries": 3},
            {"task_id": "task-cat-c", "decision_category": "C", "retry_count": 0, "max_retries": 3},
        ]

        recovered = await tm.recover_orphaned_tasks(queue=mock_queue, older_than_seconds=300.0)

        # Category A should be recovered and enqueued
        self.assertIn("task-cat-a", recovered)
        mock_queue.enqueue.assert_called_once_with("task-cat-a", {"recovery": "true", "retry": "1"})

        # Category C should NOT be blindly re-enqueued (requires approval)
        self.assertNotIn("task-cat-c", recovered)

    async def test_resume_emergency_tasks_selective(self):
        tm = TaskManager("postgresql://mock/db")
        mock_conn = AsyncMock()
        mock_pool = MagicMock()
        mock_pool.acquire.return_value.__aenter__.return_value = mock_conn
        tm.pool = mock_pool
        mock_queue = AsyncMock()

        # Task 1: Category A
        # Task 2: Category C mid-execution (step="executing_sql")
        mock_conn.fetch.return_value = [
            {"task_id": "task-resume-a", "decision_category": "A", "current_step": "executing"},
            {"task_id": "task-resume-c", "decision_category": "C", "current_step": "executing_sql"},
        ]

        resumed = await tm.resume_emergency_tasks(queue=mock_queue)

        # Category A is resumed
        self.assertIn("task-resume-a", resumed)
        mock_queue.enqueue.assert_called_once_with("task-resume-a", {"resumed_from_emergency": "true"})

        # Category C mid-execution is NOT blindly resumed
        self.assertNotIn("task-resume-c", resumed)


class TestCircuitBreakerAndWorker(unittest.IsolatedAsyncioTestCase):
    """Test ModelRouter CircuitBreaker and Worker emergency stop enforcement."""

    async def test_circuit_breaker_transitions(self):
        from core.model_router import CircuitBreaker

        cb = CircuitBreaker(failure_threshold=3, cooldown_seconds=10.0)
        self.assertFalse(cb.is_open("openai"))

        await cb.record_failure("openai")
        await cb.record_failure("openai")
        self.assertFalse(cb.is_open("openai"))

        await cb.record_failure("openai")
        self.assertTrue(cb.is_open("openai"))

        await cb.record_success("openai")
        self.assertFalse(cb.is_open("openai"))

    async def test_worker_emergency_stop_enforcement(self):
        from core.worker import Worker

        mock_tm = AsyncMock()
        mock_tm.is_emergency_stop_active.return_value = True
        mock_queue = AsyncMock()
        mock_orch = AsyncMock()

        worker = Worker(name="test-worker")
        worker.tm = mock_tm
        worker.queue = mock_queue
        worker.orch = mock_orch
        await worker._run_task("task-test-999")

        mock_tm.set_status.assert_called_once_with(
            "task-test-999", TaskStatus.PAUSED, error="Global emergency stop engaged"
        )
        mock_orch.run_task.assert_not_called()


class TestConversationMemoryAndSecurity(unittest.IsolatedAsyncioTestCase):
    """Test ConversationMemory secret redaction and null safety."""

    async def test_redact_secrets(self):
        from core.conversation_memory import redact_secrets

        raw = "My OpenAI key is sk-abcdef1234567890abcdef1234567890 and bot is 123456789:ABCDefGhiJklMnoPqrStuVwXyz123456789"
        clean = redact_secrets(raw)
        self.assertNotIn("sk-abcdef1234567890", clean)
        self.assertNotIn("123456789:ABC", clean)
        self.assertIn("[REDACTED_SECRET]", clean)

    async def test_conversation_memory_none_pool(self):
        from core.conversation_memory import ConversationMemory

        cm = ConversationMemory(pool=None)
        # Should not raise exception
        await cm.ensure()
        await cm.append("user1", "chat1", "user", "secret: sk-12345678901234567890")
        hist = await cm.render("user1", "chat1")
        self.assertEqual(hist, "")
        await cm.clear("user1", "chat1")


class TestNaturalLanguageAndPersona(unittest.TestCase):
    """Test natural language command triggers and JARVIS persona specification."""

    def test_regex_matching_for_calling_and_emergency(self):
        from telegram_bot.handlers import _CALL_REQUEST_RE, _EMERGENCY_STOP_RE

        self.assertTrue(_CALL_REQUEST_RE.search("/call"))
        self.assertTrue(_CALL_REQUEST_RE.search("Menga qo'ng'iroq qil"))
        self.assertTrue(_CALL_REQUEST_RE.search("Please call me right now"))
        self.assertTrue(_CALL_REQUEST_RE.search("Позвони мне"))
        self.assertTrue(_CALL_REQUEST_RE.search("Meni chaqir"))

        self.assertTrue(_EMERGENCY_STOP_RE.search("/stop_all"))
        self.assertTrue(_EMERGENCY_STOP_RE.search("/kill_switch"))
        self.assertTrue(_EMERGENCY_STOP_RE.search("Emergency stop immediately"))
        self.assertTrue(_EMERGENCY_STOP_RE.search("Hamma narsani to'xtat"))
        self.assertTrue(_EMERGENCY_STOP_RE.search("Останови всё"))

    def test_media_and_website_regex(self):
        from telegram_bot.handlers import _IMAGE_GEN_RE, _VIDEO_GEN_RE, _WEBSITE_GEN_RE, _FULLSTACK_GEN_RE

        self.assertTrue(_IMAGE_GEN_RE.search("Kosmik kema rasmini chizib ber"))
        self.assertTrue(_VIDEO_GEN_RE.search("Quyosh botishi haqida video yarat"))
        self.assertTrue(_WEBSITE_GEN_RE.search("Zamonaviy startup veb sayt yaratib ber"))
        self.assertTrue(_FULLSTACK_GEN_RE.search("Create a fullstack app with react and fastapi"))

    def test_persona_integrity_and_intellectual_honesty(self):
        from telegram_bot.handlers import _get_current_persona

        facts = ["Foydalanuvchi ismi: Karimullo", "Kasbi: Dasturchi"]
        persona = _get_current_persona(facts)

        self.assertIn("2026", persona)
        self.assertIn("INTELLECTUAL HONESTY", persona)
        self.assertIn("Foydalanuvchining ko'nglini olish uchungina", persona)
        self.assertIn("Hech qachon soxta manbalar", persona)
        self.assertIn("9 ADAPTIVE MODES", persona)
        self.assertIn("Ustoz va Murabbiy", persona)
        self.assertIn("Tanqidiy Fikrlovchi", persona)
        self.assertIn("Avtonom Dasturchi", persona)
        self.assertIn("Strategik Maslahatchi", persona)
        self.assertIn("Karimullo", persona)


if __name__ == "__main__":
    unittest.main()
