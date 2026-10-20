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
    """Test Telegram Voice Calling subsystem and honest diagnostics."""

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
        self.cm.config.enabled = False
        status = self.cm.get_status()
        self.assertEqual(status["state"], CallState.DISABLED.value)

    def test_is_ready_to_call_unauthorized_user(self):
        self.cm._mtproto_available = True
        ok, reason = self.cm.is_ready_to_call(recipient_id=555555)  # Not authorized
        self.assertFalse(ok)
        self.assertIn("Calling restricted", reason)

    def test_is_ready_to_call_cooldown(self):
        self.cm._mtproto_available = True
        self.cm.last_call_time = time.time()  # Just called
        ok, reason = self.cm.is_ready_to_call(recipient_id=987654321)
        self.assertFalse(ok)
        self.assertIn("cooldown active", reason.lower())

    def test_enable_and_disable_security(self):
        # Unauthorized user cannot enable/disable
        ok, msg = self.cm.disable(user_id=123)
        self.assertFalse(ok)
        self.assertIn("Permission denied", msg)

        # Authorized owner can disable
        ok, msg = self.cm.disable(user_id=987654321)
        self.assertTrue(ok)
        self.assertFalse(self.cm.config.enabled)

        # Authorized owner can enable
        ok, msg = self.cm.enable(user_id=987654321)
        self.assertTrue(ok)
        self.assertTrue(self.cm.config.enabled)

    def test_dry_run_test_call(self):
        res = self.cm.test_call(recipient_id=987654321)
        self.assertIn("test_passed", res)
        self.assertIn("readiness_reason", res)
        self.assertIn("status_details", res)

    def test_singleton_get_calling_manager(self):
        cm1 = get_calling_manager()
        cm2 = get_calling_manager()
        self.assertIs(cm1, cm2)


class TestTaskManagerAndEmergencyStop(unittest.IsolatedAsyncioTestCase):
    """Test TaskManager contract extensions and global emergency stop."""

    async def test_emergency_stop_lifecycle(self):
        tm = TaskManager("postgresql://mock/db")
        mock_conn = AsyncMock()
        mock_pool = MagicMock()
        mock_pool.acquire.return_value.__aenter__.return_value = mock_conn
        tm.pool = mock_pool

        # Set emergency stop True
        await tm.set_emergency_stop(True, user_id="user_admin", reason="Test Kill Switch")
        self.assertTrue(mock_conn.execute.called)

        # Mock is_emergency_stop_active returning True
        mock_conn.fetchrow.return_value = {"value_bool": True}
        active = await tm.is_emergency_stop_active()
        self.assertTrue(active)

        # Mock is_emergency_stop_active returning False
        mock_conn.fetchrow.return_value = {"value_bool": False}
        active = await tm.is_emergency_stop_active()
        self.assertFalse(active)

    async def test_mark_notified(self):
        tm = TaskManager("postgresql://mock/db")
        mock_conn = AsyncMock()
        mock_pool = MagicMock()
        mock_pool.acquire.return_value.__aenter__.return_value = mock_conn
        tm.pool = mock_pool

        await tm.mark_notified("task-12345")
        mock_conn.execute.assert_called_once()
        query = mock_conn.execute.call_args[0][0]
        self.assertIn("UPDATE tasks SET notified_at=$2", query)


class TestCircuitBreakerAndWorker(unittest.IsolatedAsyncioTestCase):
    """Test ModelRouter CircuitBreaker and Worker emergency stop enforcement."""

    async def test_circuit_breaker_transitions(self):
        from core.model_router import CircuitBreaker

        cb = CircuitBreaker(failure_threshold=3, cooldown_seconds=10.0)
        self.assertFalse(cb.is_open("openai"))

        # 2 failures -> not open yet
        await cb.record_failure("openai")
        await cb.record_failure("openai")
        self.assertFalse(cb.is_open("openai"))

        # 3rd failure -> open
        await cb.record_failure("openai")
        self.assertTrue(cb.is_open("openai"))

        # Success resets circuit
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

        # Worker must have set task status to PAUSED and never invoked orchestrator
        mock_tm.set_status.assert_called_once_with(
            "task-test-999", TaskStatus.PAUSED, error="Global emergency stop engaged"
        )
        mock_orch.run_task.assert_not_called()


class TestNaturalLanguageAndPersona(unittest.TestCase):
    """Test natural language command triggers and JARVIS persona specification."""

    def test_regex_matching_for_calling_and_emergency(self):
        from telegram_bot.handlers import _CALL_REQUEST_RE, _EMERGENCY_STOP_RE

        # Calling queries
        self.assertTrue(_CALL_REQUEST_RE.search("/call"))
        self.assertTrue(_CALL_REQUEST_RE.search("Menga qo'ng'iroq qil"))
        self.assertTrue(_CALL_REQUEST_RE.search("Please call me right now"))
        self.assertTrue(_CALL_REQUEST_RE.search("Позвони мне"))
        self.assertTrue(_CALL_REQUEST_RE.search("Meni chaqir"))

        # Emergency stop queries
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

        # 2026 check
        self.assertIn("2026", persona)
        # Intellectual honesty rules
        self.assertIn("INTELLECTUAL HONESTY", persona)
        self.assertIn("Foydalanuvchining ko'nglini olish uchungina", persona)
        self.assertIn("Hech qachon soxta manbalar", persona)
        # 9 Adaptive modes
        self.assertIn("9 ADAPTIVE MODES", persona)
        self.assertIn("Ustoz va Murabbiy", persona)
        self.assertIn("Tanqidiy Fikrlovchi", persona)
        self.assertIn("Avtonom Dasturchi", persona)
        self.assertIn("Strategik Maslahatchi", persona)
        # Facts inclusion
        self.assertIn("Karimullo", persona)


if __name__ == "__main__":
    unittest.main()
