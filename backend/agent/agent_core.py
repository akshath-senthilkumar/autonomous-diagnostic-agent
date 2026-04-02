"""
agent/agent_core.py — Autonomous Diagnostic Agent Core

Implements the agentic loop using Google GenAI API (google.genai).
The agent runs autonomously: it reasons, calls tools, processes results,
and continues until the diagnostic cycle is complete.

No human in the loop.
"""
from google import genai
from google.genai import types
import asyncio
import aiosqlite
import json
import logging
import os
import time
import uuid
from typing import Any, Callable, Dict, List, Optional

from agent.prompts import SYSTEM_PROMPT, TRIGGER_PROMPT_TEMPLATE
from agent.tools import TOOL_FUNCTIONS
from db.database import get_db_path

logger = logging.getLogger(__name__)

MAX_AGENT_ITERATIONS = 20    # Hard limit to prevent runaway loops
AGENT_MODEL          = "gemini-pro-latest"


class DiagnosticAgent:
    """
    Autonomous embedded systems diagnostic agent.
    
    Lifecycle:
      1. Triggered by anomaly detector with a sensor_id + preliminary reading
      2. Runs agentic loop: LLM reasons → calls tools → processes results
      3. Loop exits when LLM returns no function calls
      4. All steps logged to DB for audit and dashboard display
    """

    def __init__(self, step_callback: Optional[Callable] = None):
        """
        Args:
            step_callback: Async callable invoked with each agent step.
        """
        api_key = os.getenv("GEMINI_API_KEY", os.getenv("ANTHROPIC_API_KEY", ""))
        # We must instantiate the proper google.genai async client
        self._client = genai.Client(api_key=api_key)
        self._step_cb  = step_callback
        self._active_incidents: set = set()

    async def _log_step(self, incident_id: str, step: Dict[str, Any]):
        """Persist agent step to DB and broadcast to WebSocket clients."""
        async with aiosqlite.connect(get_db_path()) as db:
            await db.execute("""
                INSERT INTO agent_steps
                    (incident_id, step_type, tool_name, tool_input, tool_output, reasoning, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                incident_id,
                step.get("type", "reasoning"),
                step.get("tool_name"),
                json.dumps(step.get("tool_input"))  if step.get("tool_input")  else None,
                json.dumps(step.get("tool_output")) if step.get("tool_output") else None,
                step.get("reasoning"),
                step.get("timestamp", time.time())
            ))
            await db.commit()

        if self._step_cb:
            try:
                await self._step_cb({
                    "type":        "agent_step",
                    "incident_id": incident_id,
                    "step":        step
                })
            except Exception as e:
                logger.warning("Step callback error: %s", e)

    async def _create_incident(
        self,
        incident_id: str,
        sensor_id:   str,
        fault_type:  str,
        severity:    str,
    ):
        """Create an incident record in the database."""
        async with aiosqlite.connect(get_db_path()) as db:
            await db.execute("""
                INSERT OR IGNORE INTO incidents
                    (incident_id, sensor_id, fault_type, severity, status, detected_at)
                VALUES (?, ?, ?, ?, 'investigating', ?)
            """, (incident_id, sensor_id, fault_type, severity, time.time()))
            await db.commit()

    async def _update_incident(self, incident_id: str, **kwargs):
        """Update incident fields."""
        if not kwargs:
            return
        set_clause = ", ".join(f"{k} = ?" for k in kwargs)
        values     = list(kwargs.values()) + [incident_id]
        async with aiosqlite.connect(get_db_path()) as db:
            await db.execute(
                f"UPDATE incidents SET {set_clause} WHERE incident_id = ?", values
            )
            await db.commit()

    async def run_diagnostic(
        self,
        sensor_id:     str,
        current_value: float,
        unit:          str,
        anomaly_score: float,
        severity:      str,
    ) -> Dict[str, Any]:
        """Main entry point — run a full autonomous diagnostic cycle."""
        incident_id = f"INC-{int(time.time())}-{str(uuid.uuid4())[:6].upper()}"
        
        if incident_id in self._active_incidents:
            logger.warning("Duplicate incident trigger ignored")
            return {}
        
        self._active_incidents.add(incident_id)
        logger.info("🤖 Agent starting diagnostic for %s [%s]", sensor_id, incident_id)

        await self._create_incident(
            incident_id, sensor_id,
            fault_type=f"anomaly_{sensor_id.lower()}",
            severity=severity
        )

        trigger_msg = TRIGGER_PROMPT_TEMPLATE.format(
            sensor_id     = sensor_id,
            current_value = current_value,
            unit          = unit,
            anomaly_score = anomaly_score,
            severity      = severity,
            incident_id   = incident_id,
            timestamp     = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        )
        
        await self._log_step(incident_id, {
            "type":      "triggered",
            "reasoning": f"Agent triggered for sensor {sensor_id}. Value={current_value}{unit}, score={anomaly_score:.3f}",
            "timestamp": time.time()
        })

        if self._step_cb:
            await self._step_cb({
                "type":        "incident_created",
                "incident_id": incident_id,
                "sensor_id":   sensor_id,
                "severity":    severity,
            })

        final_summary = {}
        iteration     = 0

        # We keep the messages conversation fully manually
        messages = [{"role": "user", "parts": [{"text": trigger_msg}]}]
        
        config = types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            tools=list(TOOL_FUNCTIONS.values())
        )

        try:
            while iteration < MAX_AGENT_ITERATIONS:
                iteration += 1
                logger.debug("Agent iteration %d for %s", iteration, incident_id)

                # Free-tier rate limiting protection
                await asyncio.sleep(3.0)
                
                try:
                    response = await self._client.aio.models.generate_content(
                        model=AGENT_MODEL,
                        contents=messages,
                        config=config
                    )
                except Exception as e:
                    if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e):
                        logger.warning("Rate limited by Google API. Waiting 6 seconds...")
                        await asyncio.sleep(6.0)
                        response = await self._client.aio.models.generate_content(
                            model=AGENT_MODEL,
                            contents=messages,
                            config=config
                        )
                    else:
                        raise e
                
                # Append the model's response to the conversation block
                if response.candidates and response.candidates[0].content:
                    messages.append(response.candidates[0].content)

                # Extract reasoning text
                text_blocks = [p.text for p in response.candidates[0].content.parts if hasattr(p, "text") and p.text]
                reasoning = "\n".join(text_blocks) if text_blocks else None

                if reasoning:
                    await self._log_step(incident_id, {
                        "type":      "reasoning",
                        "reasoning": reasoning,
                        "timestamp": time.time()
                    })

                # Check for function calls
                function_calls = response.function_calls

                if not function_calls:
                    logger.info("✅ Agent completed diagnostic for %s", incident_id)
                    final_summary = {
                        "incident_id": incident_id,
                        "status":      "complete",
                        "iterations":  iteration,
                        "reasoning":   reasoning
                    }
                    break

                # We have function calls, execute them
                tool_results_parts = await self._execute_tools_genai(function_calls, incident_id)
                # Feed tool results as the next user message
                messages.append({
                    "role": "user",
                    "parts": tool_results_parts
                })

        except Exception as e:
            logger.error("Unexpected agent error: %s", e, exc_info=True)
            await self._update_incident(incident_id, status="error")
        finally:
            self._active_incidents.discard(incident_id)

        await self._update_incident(incident_id, status="resolved", resolved_at=time.time())

        if self._step_cb:
            await self._step_cb({
                "type":        "incident_resolved",
                "incident_id": incident_id,
            })

        return final_summary

    async def _execute_tools_genai(
        self,
        function_calls: List[Any],
        incident_id: str,
    ) -> List[Any]:
        """Execute all tool calls concurrently for Google GenAI SDK."""
        async def run_one(call) -> Any:
            tool_name  = call.name
            tool_input = dict(call.args) if call.args else {}

            await self._log_step(incident_id, {
                "type":       "tool_call",
                "tool_name":  tool_name,
                "tool_input": tool_input,
                "timestamp":  time.time()
            })

            fn = TOOL_FUNCTIONS.get(tool_name)
            if not fn:
                result = {"error": f"Unknown tool: {tool_name}"}
            else:
                try:
                    result = await fn(**tool_input)
                except Exception as e:
                    logger.error("Tool %s error: %s", tool_name, e, exc_info=True)
                    result = {"error": str(e)}

            await self._log_step(incident_id, {
                "type":        "tool_result",
                "tool_name":   tool_name,
                "tool_output": result,
                "timestamp":   time.time()
            })

            if tool_name == "diagnose_fault" and "root_cause" in result:
                await self._update_incident(
                    incident_id,
                    fault_type = result.get("fault_class", "unknown"),
                    diagnosis  = result.get("root_cause", ""),
                )
            elif tool_name == "trigger_corrective_action" and result.get("success"):
                await self._update_incident(
                    incident_id,
                    corrective_action = result.get("action_type", "")
                )

            # Py_Numpy JSON serialization sanitizer
            def _sanitize(val):
                if isinstance(val, dict): return {k: _sanitize(v) for k, v in val.items()}
                elif isinstance(val, list): return [_sanitize(v) for v in val]
                elif hasattr(val, "item"): return val.item()
                return val

            clean_result = _sanitize(result)

            return types.Part.from_function_response(
                name=tool_name,
                response={"result": clean_result}
            )

        results_parts = await asyncio.gather(*[run_one(call) for call in function_calls])
        return list(results_parts)


# ──────────────────────────────────────────────────────────────
# Anomaly Monitor — watches sensor stream, triggers agent
# ──────────────────────────────────────────────────────────────

class AnomalyMonitor:
    COOLDOWN_SECONDS = 60

    def __init__(self, agent: DiagnosticAgent):
        self._agent     = agent
        self._last_trigger: Dict[str, float] = {}
        self._reading_buffer: Dict[str, List] = {}
        self._buffer_size = 30

    async def on_reading(self, reading) -> Optional[Dict]:
        sid = reading.sensor_id

        buf = self._reading_buffer.setdefault(sid, [])
        buf.append(reading.value)
        if len(buf) > self._buffer_size:
            buf.pop(0)

        score, severity = self._quick_check(reading, buf)
        if score < 0.4:
            return None

        now  = time.time()
        last = self._last_trigger.get(sid, 0)
        if now - last < self.COOLDOWN_SECONDS:
            return None

        self._last_trigger[sid] = now

        logger.info(
            "⚠️  Preliminary anomaly on %s: value=%.3f score=%.3f severity=%s",
            sid, reading.value, score, severity
        )

        asyncio.create_task(
            self._agent.run_diagnostic(
                sensor_id     = sid,
                current_value = reading.value,
                unit          = reading.unit,
                anomaly_score = score,
                severity      = severity,
            ),
            name=f"diag_{sid}"
        )

        return {"triggered": True, "sensor_id": sid, "score": score}

    def _quick_check(self, reading, buf: List[float]) -> tuple[float, str]:
        from agent.tools import SENSOR_THRESHOLDS
        import numpy as np

        value      = reading.value
        thresholds = SENSOR_THRESHOLDS.get(reading.sensor_id, {})
        score      = 0.0

        if thresholds:
            if value > thresholds.get("critical_max", float("inf")):
                return 0.95, "critical"
            if value > thresholds.get("max", float("inf")):
                score = max(score, 0.6)
            elif value < thresholds.get("min", float("-inf")):
                score = max(score, 0.55)

        if len(buf) >= 10:
            arr    = np.array(buf)
            mean   = np.mean(arr[:-1])
            std    = np.std(arr[:-1])
            if std > 0:
                z = abs(value - mean) / std
                score = max(score, min(1.0, z / 5.0))

        if score >= 0.8:
            severity = "critical"
        elif score >= 0.6:
            severity = "high"
        elif score >= 0.4:
            severity = "medium"
        else:
            severity = "low"

        return score, severity