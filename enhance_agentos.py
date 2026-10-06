from pathlib import Path
import json

def patch_file(name, fn):
    p = Path(name)
    s = p.read_text(encoding="utf-8")
    s2 = fn(s)
    if s2 != s:
        p.write_text(s2, encoding="utf-8")
        print("patched", name)

def worker(s):
    if "_monitor_task_progress" in s:
        return s
    marker = '        await self.tm.set_status(task_id, TaskStatus.RUNNING, current_step="starting")'
    helper = '''
    async def _monitor_task_progress(self, task_id: str) -> None:
        seen = 0
        step_ok = 0
        plan_steps = 0
        while True:
            try:
                evs = await self.events.get_events("task:" + task_id, after_version=seen)
                if evs:
                    seen = evs[-1].version
                for ev in evs:
                    et = ev.type
                    data = ev.data or {}
                    if et == "kernel.started":
                        await self.tm.set_status(task_id, TaskStatus.RUNNING, current_step="starting", progress=0.05)
                    elif et == "kernel.perceived":
                        await self.tm.set_status(task_id, TaskStatus.RUNNING, current_step="understanding", progress=0.12)
                    elif et == "kernel.planned":
                        plan_steps = max(plan_steps, len(data.get("steps") or []))
                        await self.tm.set_status(task_id, TaskStatus.RUNNING, current_step="planning", progress=0.20)
                    elif et == "kernel.step_ok":
                        step_ok += 1
                        denom = max(plan_steps, step_ok + 1)
                        progress = min(0.88, 0.20 + 0.65 * (step_ok / denom))
                        await self.tm.set_status(task_id, TaskStatus.RUNNING, current_step=str(data.get("step") or "executing"), progress=progress)
                    elif et == "kernel.reflection":
                        await self.tm.set_status(task_id, TaskStatus.RUNNING, current_step="self-correcting", progress=0.72)
                    elif et == "kernel.handoff":
                        await self.tm.set_status(task_id, TaskStatus.RUNNING, current_step="delegating", progress=0.55)
                    elif et == "kernel.completed":
                        await self.tm.set_status(task_id, TaskStatus.RUNNING, current_step="finalizing", progress=0.95)
            except Exception as exc:
                log.debug("progress monitor: %s", exc)
            row = await self.tm.get_task(task_id)
            if row and row.get("status") in ("COMPLETED", "FAILED", "CANCELLED"):
                return
            await asyncio.sleep(1)

'''
    s = s.replace(marker, helper + marker, 1)
    s = s.replace(marker, '        await self.tm.set_status(task_id, TaskStatus.RUNNING, current_step="starting", progress=0.05)', 1)
    old = '        try:\n            # Snapshot before running.'
    new = '        monitor = asyncio.create_task(self._monitor_task_progress(task_id))\n        try:\n            # Snapshot before running.'
    s = s.replace(old, new, 1)
    old = '            result: TaskResult = await self.orch.run_task(task, entry_agent)\n            # Save result and status.'
    new = '            result: TaskResult = await self.orch.run_task(task, entry_agent)\n            artifacts = result.artifacts or []\n            result_text = result.summary or ""\n            if artifacts:\n                result_text += "\\\\n\\\\n__ARTIFACTS_JSON__\\\\n" + json.dumps(artifacts, ensure_ascii=False, default=str)\n            # Save result and status.'
    s = s.replace(old, new, 1)
    s = s.replace("result=result.summary,\n                                         progress=1.0", "result=result_text,\n                                         progress=1.0", 1)
    s = s.replace("result=result.summary)\n            await self.tm.save_checkpoint", "result=result_text, progress=1.0)\n            await self.tm.save_checkpoint", 1)
    return s

def notifier(s):
    if "send_artifact" in s:
        return s
    s = s.replace("import logging, os", "import logging, os, mimetypes\nfrom pathlib import Path", 1)
    s = s.replace("from aiogram.enums import ParseMode", "from aiogram.enums import ParseMode\nfrom aiogram.types import FSInputFile", 1)
    anchor = "\n\n_notifier:"
    method = '''
    async def send_artifact(self, chat_id: int | str, artifact: dict) -> None:
        if not self._bot:
            return
        url = artifact.get("url") or artifact.get("link") or artifact.get("download_url")
        path = artifact.get("path") or artifact.get("file_path")
        name = str(artifact.get("name") or artifact.get("filename") or "artifact")
        kind = str(artifact.get("type") or artifact.get("kind") or "").lower()
        try:
            if url:
                await self._bot.send_message(int(chat_id), "Artifact: " + name + "\\n" + url)
                return
            if not path:
                return
            p = Path(str(path))
            if not p.exists():
                return
            mime = mimetypes.guess_type(p.name)[0] or ""
            if kind in ("image", "photo") or mime.startswith("image/"):
                await self._bot.send_photo(int(chat_id), FSInputFile(p), caption=name[:1000])
            elif kind in ("audio", "music") or mime.startswith("audio/"):
                await self._bot.send_audio(int(chat_id), FSInputFile(p), caption=name[:1000])
            elif kind == "video" or mime.startswith("video/"):
                await self._bot.send_video(int(chat_id), FSInputFile(p), caption=name[:1000])
            else:
                await self._bot.send_document(int(chat_id), FSInputFile(p), caption=name[:1000])
        except Exception as exc:
            log.warning("artifact delivery failed: %s", exc)
'''
    return s.replace(anchor, method + anchor, 1)

def bot_patch(s):
    if "__ARTIFACTS_JSON__" not in s:
        start = s.find("async def _status_notifier_loop")
        end = s.find("\n\nif __name__ == '__main__':", start)
        if end < 0:
            end = s.find('\n\nif __name__ == "__main__":', start)
        if start >= 0 and end >= 0:
            fn = '''async def _status_notifier_loop(tm: TaskManager, notifier) -> None:
    seen = set()
    while True:
        try:
            async with tm.pool.acquire() as c:
                rows = await c.fetch("SELECT * FROM tasks WHERE status IN ('COMPLETED','FAILED','WAITING_APPROVAL') ORDER BY updated_at DESC LIMIT 50")
            for r in rows:
                key = str(r["task_id"]) + ":" + str(r["status"])
                if key in seen:
                    continue
                seen.add(key)
                chat_id = r.get("chat_id")
                if not chat_id:
                    continue
                short = str(r["task_id"])[:8]
                if r["status"] == "FAILED":
                    await notifier.send(chat_id, "❌ Task failed " + short + "\\n\\nError: " + str(r.get("error") or "unknown")[:2500])
                    continue
                if r["status"] == "WAITING_APPROVAL":
                    await notifier.send(chat_id, "🔐 Task " + short + " is waiting for approval.")
                    continue
                raw = r.get("result") or ""
                artifact_json = ""
                if "__ARTIFACTS_JSON__" in raw:
                    raw, artifact_json = raw.split("__ARTIFACTS_JSON__", 1)
                elapsed = (r.get("completed_at") or 0) - (r.get("started_at") or 0)
                await notifier.send(chat_id, "✅ Task completed " + short + "\\n⏱ " + str(int(elapsed)) + "s\\n\\n" + raw.strip()[:3000])
                try:
                    artifacts = json.loads(artifact_json.strip()) if artifact_json.strip() else []
                except Exception:
                    artifacts = []
                for artifact in artifacts:
                    if isinstance(artifact, dict):
                        await notifier.send_artifact(chat_id, artifact)
        except Exception as exc:
            log.debug("notifier loop: %s", exc)
        await asyncio.sleep(5)
'''
            s = s[:start] + fn + s[end:]
    if "BotCommand" not in s:
        s = s.replace("from aiogram import Bot, Dispatcher", "from aiogram import Bot, Dispatcher\nfrom aiogram.types import BotCommand", 1)
    if "set_my_commands" not in s:
        menu = '''    await bot.set_my_commands([
        BotCommand(command="start", description="Open AgentOS"),
        BotCommand(command="task", description="Create a task"),
        BotCommand(command="tasks", description="My tasks"),
        BotCommand(command="status", description="System status"),
        BotCommand(command="agents", description="AI agents"),
        BotCommand(command="pause", description="Pause task"),
        BotCommand(command="resume", description="Resume task"),
        BotCommand(command="cancel", description="Cancel task"),
        BotCommand(command="retry", description="Retry task"),
        BotCommand(command="help", description="Help"),
    ])
'''
        s = s.replace("    dp = Dispatcher()", "    dp = Dispatcher()\n" + menu, 1)
    return s

patch_file("telegram_bot/bot.py", bot_patch)

patch_file("core/worker.py", worker)
patch_file("telegram_bot/notifier.py", notifier)
print("enhancement patch complete")
