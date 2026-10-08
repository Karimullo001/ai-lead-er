from __future__ import annotations
import json
from typing import List, Dict

SKILLS: Dict[str, str] = {
    "polymath": "cross-domain synthesis",
    "business_coach": "business strategy and decisions",
    "startup_builder": "startup, product and execution",
    "idea_creative": "creative ideation and alternatives",
    "security": "defensive security and secure engineering",
    "biohacker": "evidence-based performance and habits",
    "engineer": "software, hardware and systems engineering",
    "psychologist": "evidence-informed behavior and communication",
    "chess_master": "chess calculation and strategy",
    "profiler": "behavioral pattern analysis from supplied evidence",
    "stoic": "Stoic decision framing",
    "dr_house": "diagnostic-style hypothesis testing without unsupported diagnosis",
    "crypto_bro": "crypto/blockchain analysis with risk disclosure",
    "global_vibes": "global culture, trends and cross-cultural context",
}

SYSTEM = """You are AgentOS Expert, the user's private polymathic executive AI.
Answer in the SAME LANGUAGE as the latest user message.
Use relevant persistent conversation/task context.
For current or changing facts, research first when a research tool is available.
Separate verified facts, inference and uncertainty. Never invent sources or results.
Before declaring work finished, verify the actual output.
Prefer fast reliable execution and parallelize independent work.
Do not ask unnecessary clarification; infer sensible defaults.
Combine multiple skills when useful.
For medical, legal, financial or security-sensitive topics, be evidence-based and explicit about uncertainty and safety limits.
Available skills: """ + json.dumps(SKILLS, ensure_ascii=False)

def select(text: str) -> List[str]:
    t = text.lower()
    aliases = {
      "business_coach":["business","strategy","negotiation"],
      "startup_builder":["startup","mvp","founder","product"],
      "idea_creative":["idea","creative","brainstorm","invent"],
      "security":["security","cyber","pentest"],
      "biohacker":["biohack","sleep","nutrition","performance"],
      "engineer":["engineer","engineering","hardware","architecture","debug"],
      "psychologist":["psychology","behavior","relationship"],
      "chess_master":["chess","shaxmat"],
      "profiler":["profile","profiling","behavioral"],
      "stoic":["stoic","stoicism"],
      "dr_house":["diagnostic","diagnose","symptom"],
      "crypto_bro":["crypto","bitcoin","ethereum","blockchain"],
      "global_vibes":["global","culture","trend","geopolitics"],
    }
    found=[k for k,v in aliases.items() if any(x in t for x in v)]
    return found or ["polymath","engineer"]

def context(text: str) -> str:
    return "\n\nEXPERT MODE:\n" + SYSTEM + "\nSelected skills: " + ", ".join(select(text))
