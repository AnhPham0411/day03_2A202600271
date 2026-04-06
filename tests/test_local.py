"""
run_tests.py — QA Test Runner (Member 4)
Branch: feature/qa-testing

Cách dùng:
    python run_tests.py --mode both        # mock (không cần API key)
    python run_tests.py --mode both --real # thật (cần OPENAI_API_KEY trong .env)
"""

import os
import sys
import json
import time
import argparse
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# ───────────────────────────────────────────────
# 10 TEST CASES
# ───────────────────────────────────────────────

TEST_CASES = [
    {"id": "TC-01", "level": 1, "description": "Kiểm tra tồn kho đơn giản",
     "input": "Kho còn iPhone 15 không?",
     "tools_expected": ["check_inventory"], "attack_type": "Baseline"},
    {"id": "TC-02", "level": 1, "description": "Kiểm tra mã giảm giá",
     "input": "Mã GIAM20 có còn hiệu lực không? Giảm bao nhiêu %?",
     "tools_expected": ["get_discount"], "attack_type": "Baseline"},
    {"id": "TC-03", "level": 1, "description": "Tính phí ship đơn giản",
     "input": "Tính phí ship hàng 2kg, khoảng cách 15km",
     "tools_expected": ["calc_shipping_fee"], "attack_type": "Baseline"},
    {"id": "TC-04", "level": 2, "description": "Mua hàng + áp mã giảm giá",
     "input": "Tôi muốn mua 1 MacBook Air M2, áp mã GIAM20. Tổng tiền hàng sau giảm là bao nhiêu?",
     "tools_expected": ["check_inventory", "get_discount"], "attack_type": "Multi-step"},
    {"id": "TC-05", "level": 2, "description": "Kiểm tra hàng + tính ship",
     "input": "Kho còn AirPods Pro không? Nếu còn, ship về nhà tôi cách 50km (nặng 0.3kg) thì tốn bao nhiêu tiền ship?",
     "tools_expected": ["check_inventory", "calc_shipping_fee"], "attack_type": "Conditional"},
    {"id": "TC-06", "level": 2, "description": "Mã không hợp lệ",
     "input": "Tôi có mã XXXXXXX, áp vào mua iPhone 15 thì giảm được bao nhiêu?",
     "tools_expected": ["get_discount", "check_inventory"], "attack_type": "Error handling"},
    {"id": "TC-07", "level": 2, "description": "Sản phẩm không tồn tại",
     "input": "Kho có Samsung Galaxy Z Fold 6 không? Áp mã GIAM20 mua 1 cái thì bao nhiêu tiền?",
     "tools_expected": ["check_inventory"], "attack_type": "Hallucination trap"},
    {"id": "TC-08", "level": 3, "description": "Full flow flagship",
     "input": "Tôi muốn mua 2 iPhone 15, áp mã GIAM20, ship về cách kho 30km (nặng 1kg mỗi cái). Tổng cộng phải trả bao nhiêu?",
     "tools_expected": ["check_inventory", "get_discount", "calc_shipping_fee"], "attack_type": "Full pipeline"},
    {"id": "TC-09", "level": 3, "description": "Chỉ tính tiền ship",
     "input": "Kho còn AirPods Pro không? Nếu còn lấy tôi 1 cái, áp mã SALE10, nhà tôi cách 50km. Chỉ tính tiền ship thôi. Ship bao nhiêu tiền?",
     "tools_expected": ["check_inventory", "get_discount", "calc_shipping_fee"], "attack_type": "Prompt attack"},
    {"id": "TC-10", "level": 3, "description": "Câu hỏi mơ hồ",
     "input": "Tôi muốn mua đồ Apple, cái nào rẻ nhất thì lấy, ship về cách 200km, áp mã SALE10. Tổng bao nhiêu?",
     "tools_expected": ["check_inventory", "get_discount", "calc_shipping_fee"], "attack_type": "Ambiguity"},
]


# ───────────────────────────────────────────────
# TOKEN HELPERS
# ───────────────────────────────────────────────

def extract_tokens_from_response(response: dict) -> dict:
    """
    Trích xuất token usage từ response của OpenAI/Anthropic.
    Trả về dict chuẩn: {prompt_tokens, completion_tokens, total_tokens}
    """
    usage = response.get("usage") or {}

    # OpenAI format: usage.prompt_tokens / completion_tokens / total_tokens
    if "prompt_tokens" in usage:
        return {
            "prompt_tokens":     usage.get("prompt_tokens", 0),
            "completion_tokens": usage.get("completion_tokens", 0),
            "total_tokens":      usage.get("total_tokens", 0),
        }

    # Anthropic format: usage.input_tokens / output_tokens
    if "input_tokens" in usage:
        prompt_tokens     = usage.get("input_tokens", 0)
        completion_tokens = usage.get("output_tokens", 0)
        return {
            "prompt_tokens":     prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens":      prompt_tokens + completion_tokens,
        }

    return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}


def accumulate_tokens(base: dict, extra: dict) -> dict:
    """Cộng dồn token từ nhiều lần gọi API (dùng cho agent multi-step)."""
    return {
        "prompt_tokens":     base["prompt_tokens"]     + extra["prompt_tokens"],
        "completion_tokens": base["completion_tokens"] + extra["completion_tokens"],
        "total_tokens":      base["total_tokens"]      + extra["total_tokens"],
    }


ZERO_TOKENS = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}


# ───────────────────────────────────────────────
# TOOL EXECUTOR
# ───────────────────────────────────────────────

def make_tool_executor():
    from src.tools.tools import check_inventory, get_discount, calc_shipping_fee, search_product

    def tool_executor(action: str, action_input: str) -> str:
        action = action.strip().lower()
        try:
            if action == "check_inventory":
                return str(check_inventory(action_input.strip()))
            elif action == "get_discount":
                return str(get_discount(action_input.strip()))
            elif action == "calc_shipping_fee":
                parts = [p.strip() for p in action_input.split(",")]
                if len(parts) != 2:
                    return f"[Lỗi] Cần 2 tham số: distance_km, weight_kg. Nhận: '{action_input}'"
                return str(calc_shipping_fee(float(parts[0]), float(parts[1])))
            elif action == "search_product":
                return str(search_product(action_input.strip()))
            else:
                return f"[Lỗi] Tool '{action}' không tồn tại."
        except Exception as e:
            return f"[Lỗi tool '{action}': {e}]"

    return tool_executor


# ───────────────────────────────────────────────
# REAL RUNNERS
# ───────────────────────────────────────────────

from Chatbot import build_chatbot

def run_real_chatbot(user_input: str) -> dict:
    bot = build_chatbot()
    start = time.time()
    result = bot.generate(prompt=user_input, system_prompt=None)
    latency_ms = int((time.time() - start) * 1000)
    output = result.get("content", str(result))

    # Lấy token từ response gốc (nếu chatbot trả về raw response kèm usage)
    tokens = extract_tokens_from_response(result if isinstance(result, dict) else {})

    return {
        "output":     str(output),
        "latency_ms": latency_ms,
        "tools_called": [],
        "steps":      0,
        "tokens":     tokens,
        "error":      None,
    }


def run_real_agent(user_input: str) -> dict:
    from src.agent.agent import ReActAgent
    from src.core.openai_provider import OpenAIProvider

    accumulated_tokens = dict(ZERO_TOKENS)

    # Wrapper provider — thu thập token sau mỗi lần gọi LLM
    class TrackingProvider:
        def __init__(self, p):
            self._p = p
            self.model_name = p.model_name

        def generate(self, prompt, system_prompt=None):
            nonlocal accumulated_tokens
            raw = self._p.generate(prompt, system_prompt=system_prompt)
            # Cộng dồn token từ lần gọi này
            step_tokens = extract_tokens_from_response(raw if isinstance(raw, dict) else {})
            accumulated_tokens = accumulate_tokens(accumulated_tokens, step_tokens)
            return raw.get("content", "") if isinstance(raw, dict) else raw

    llm = TrackingProvider(
        OpenAIProvider(model_name="gpt-4o-mini", api_key=os.getenv("OPENAI_API_KEY"))
    )

    tools_called = []
    base_executor = make_tool_executor()

    def tracking_executor(action, action_input):
        tools_called.append(action)
        return base_executor(action, action_input)

    agent = ReActAgent(llm=llm, tool_executor=tracking_executor, max_steps=8)
    start = time.time()
    output = agent.run(user_input)
    latency_ms = int((time.time() - start) * 1000)

    return {
        "output":       str(output),
        "latency_ms":   latency_ms,
        "tools_called": tools_called,
        "steps":        len(tools_called),
        "tokens":       accumulated_tokens,
        "error":        None,
    }


# ───────────────────────────────────────────────
# MOCK RUNNERS
# ───────────────────────────────────────────────

def run_mock_chatbot(user_input: str) -> dict:
    time.sleep(0.2)
    return {
        "output":       f"[MOCK CHATBOT] '{user_input[:50]}'",
        "latency_ms":   200,
        "tools_called": [],
        "steps":        0,
        "tokens":       {"prompt_tokens": 120, "completion_tokens": 80, "total_tokens": 200},
        "error":        None,
    }


def run_mock_agent(user_input: str) -> dict:
    time.sleep(0.3)
    return {
        "output":       f"[MOCK AGENT] '{user_input[:50]}'",
        "latency_ms":   300,
        "tools_called": ["check_inventory"],
        "steps":        1,
        "tokens":       {"prompt_tokens": 300, "completion_tokens": 150, "total_tokens": 450},
        "error":        None,
    }


# ───────────────────────────────────────────────
# LOGGER & MAIN
# ───────────────────────────────────────────────

def _fmt_tokens(tokens: dict) -> str:
    """Định dạng ngắn gọn cho bảng Markdown: vào↑ / ra↓ / tổng"""
    if not tokens or tokens.get("total_tokens", 0) == 0:
        return "—"
    return f"{tokens['prompt_tokens']}↑ {tokens['completion_tokens']}↓ (tổng {tokens['total_tokens']})"


def run_suite(mode: str, use_mock: bool):
    os.makedirs("tests/results", exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    chatbot_results: dict = {}
    agent_results:   dict = {}

    print(f"\n{'='*60}")
    print(f"  QA TEST SUITE — {'MOCK' if use_mock else 'REAL'} | {mode.upper()}")
    print(f"{'='*60}\n")

    for tc in TEST_CASES:
        tid, user_input = tc["id"], tc["input"]
        print(f"[{tid}] L{tc['level']} | {tc['attack_type']}")
        print(f"  ➜ {user_input[:75]}")

        if mode in ("chatbot", "both"):
            try:
                res = run_mock_chatbot(user_input) if use_mock else run_real_chatbot(user_input)
                chatbot_results[tid] = res
                tok = res.get("tokens", ZERO_TOKENS)
                print(
                    f"  🤖 Chatbot ({res['latency_ms']}ms | "
                    f"tokens: tổng {tok['total_tokens']}): {str(res['output'])[:70]}"
                )
            except Exception as e:
                chatbot_results[tid] = {
                    "output": str(e), "error": str(e),
                    "latency_ms": 0, "tools_called": [], "steps": 0,
                    "tokens": dict(ZERO_TOKENS),
                }
                print(f"  🤖 Chatbot ❌ {e}")

        if mode in ("agent", "both"):
            try:
                res = run_mock_agent(user_input) if use_mock else run_real_agent(user_input)
                agent_results[tid] = res
                expected = set(tc["tools_expected"])
                actual   = set(res.get("tools_called", []))
                mark = "✅" if expected.issubset(actual) else "❌"
                tok  = res.get("tokens", ZERO_TOKENS)
                print(
                    f"  🧠 Agent  ({res['latency_ms']}ms | "
                    f"tools: {res['tools_called']} | "
                    f"tokens: tổng {tok['total_tokens']}) {mark}"
                )
                print(f"           {str(res['output'])[:80]}")
            except Exception as e:
                agent_results[tid] = {
                    "output": str(e), "error": str(e),
                    "latency_ms": 0, "tools_called": [], "steps": 0,
                    "tokens": dict(ZERO_TOKENS),
                }
                print(f"  🧠 Agent  ❌ {e}")
        print()

    # ── Save JSON ──────────────────────────────
    all_results = list(chatbot_results.values()) + list(agent_results.values())
    json_path = f"tests/results/run_{ts}.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)
    print(f"📁 JSON → {json_path}")

    # ── Save Markdown ──────────────────────────
    md = [
        f"# Test Run — {datetime.now().strftime('%Y-%m-%d %H:%M')}\n",
        "> **Token format:** `vào↑ ra↓ (tổng)`\n",
        "| TC | Level | Attack | CB ms | CB Tokens | AG ms | AG Tokens | Tools gọi | Pass |",
        "|:---|:------|:-------|------:|:----------|------:|:----------|:----------|:----:|",
    ]

    for tc in TEST_CASES:
        tid = tc["id"]
        cb  = chatbot_results.get(tid, {})
        ag  = agent_results.get(tid, {})

        cb_tok  = _fmt_tokens(cb.get("tokens"))
        ag_tok  = _fmt_tokens(ag.get("tokens"))
        tools   = ", ".join(ag.get("tools_called", [])) or "—"
        passed  = "✅" if set(tc["tools_expected"]).issubset(set(ag.get("tools_called", []))) else "❌"

        md.append(
            f"| {tid} | L{tc['level']} | {tc['attack_type']} "
            f"| {cb.get('latency_ms','?')}ms | {cb_tok} "
            f"| {ag.get('latency_ms','?')}ms | {ag_tok} "
            f"| {tools} | {passed} |"
        )

    # ── Token summary block ────────────────────
    md.append("\n## Tổng hợp Token\n")
    md.append("| Chỉ số | Chatbot | Agent |")
    md.append("|:-------|--------:|------:|")

    def total_field(results: dict, field: str) -> int:
        return sum(r.get("tokens", {}).get(field, 0) for r in results.values())

    cb_prompt = total_field(chatbot_results, "prompt_tokens")
    cb_comp   = total_field(chatbot_results, "completion_tokens")
    cb_total  = total_field(chatbot_results, "total_tokens")
    ag_prompt = total_field(agent_results,   "prompt_tokens")
    ag_comp   = total_field(agent_results,   "completion_tokens")
    ag_total  = total_field(agent_results,   "total_tokens")

    n_cb = len(chatbot_results) or 1
    n_ag = len(agent_results)   or 1

    md.append(f"| Prompt tokens (tổng)      | {cb_prompt:,} | {ag_prompt:,} |")
    md.append(f"| Completion tokens (tổng)  | {cb_comp:,}   | {ag_comp:,}   |")
    md.append(f"| **Total tokens (tổng)**   | **{cb_total:,}** | **{ag_total:,}** |")
    md.append(f"| Avg total / test case     | {cb_total // n_cb:,} | {ag_total // n_ag:,} |")

    # ── Chi tiết từng TC ──────────────────────
    md.append("\n## Chi tiết\n")
    for tc in TEST_CASES:
        tid = tc["id"]
        cb  = chatbot_results.get(tid, {})
        ag  = agent_results.get(tid, {})
        cb_tok_raw = cb.get("tokens", ZERO_TOKENS)
        ag_tok_raw = ag.get("tokens", ZERO_TOKENS)

        md += [
            f"### {tid} — {tc['description']}",
            f"**Input**: `{tc['input']}`",
            "",
            f"**Chatbot**",
            f"- Output: {cb.get('output', 'N/A')}",
            f"- Latency: {cb.get('latency_ms', '?')}ms",
            f"- Tokens: prompt={cb_tok_raw.get('prompt_tokens',0)} | "
            f"completion={cb_tok_raw.get('completion_tokens',0)} | "
            f"total={cb_tok_raw.get('total_tokens',0)}",
            "",
            f"**Agent**",
            f"- Output: {ag.get('output', 'N/A')}",
            f"- Latency: {ag.get('latency_ms', '?')}ms",
            f"- Tools: {ag.get('tools_called', [])} | Bước: {ag.get('steps', '?')}",
            f"- Tokens: prompt={ag_tok_raw.get('prompt_tokens',0)} | "
            f"completion={ag_tok_raw.get('completion_tokens',0)} | "
            f"total={ag_tok_raw.get('total_tokens',0)}",
            "\n---\n",
        ]

    md_path = f"tests/results/run_{ts}.md"
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(md))
    print(f"📄 Markdown → {md_path}")

    # ── Console summary ────────────────────────
    if agent_results:
        passed_cnt = sum(
            1 for tc in TEST_CASES
            if set(tc["tools_expected"]).issubset(
                set(agent_results.get(tc["id"], {}).get("tools_called", []))
            )
        )
        errors = sum(1 for r in agent_results.values() if r.get("error"))
        avg_ms = sum(r["latency_ms"] for r in agent_results.values()) / len(agent_results)
        print(f"\n  Agent : Pass {passed_cnt}/{len(TEST_CASES)} | Errors {errors} | Avg latency {avg_ms:.0f}ms")
        print(f"  Tokens: Chatbot tổng={cb_total:,} | Agent tổng={ag_total:,} "
              f"(trung bình CB={cb_total//n_cb:,} / AG={ag_total//n_ag:,} mỗi test)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["agent", "chatbot", "both"], default="both")
    parser.add_argument("--real", action="store_true")
    args = parser.parse_args()
    run_suite(mode=args.mode, use_mock=not args.real)