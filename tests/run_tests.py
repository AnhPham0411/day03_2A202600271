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
    # generate trả về dict với 'content'
    result = bot.generate(prompt=user_input, system_prompt=None)
    latency_ms = int((time.time() - start) * 1000)
    output = result.get("content", str(result))
    return {
        "output": str(output),
        "latency_ms": latency_ms,
        "tools_called": [],
        "steps": 0,
        "error": None
    }

def run_real_agent(user_input: str) -> dict:
    from src.agent.agent import ReActAgent
    from src.core.openai_provider import OpenAIProvider

    # Wrapper: generate() trả string thay vì dict
    class StringProvider:
        def __init__(self, p):
            self._p = p
            self.model_name = p.model_name
        def generate(self, prompt, system_prompt=None):
            r = self._p.generate(prompt, system_prompt=system_prompt)
            return r.get("content", "") if isinstance(r, dict) else r

    llm = StringProvider(OpenAIProvider(model_name="gpt-4o-mini", api_key=os.getenv("OPENAI_API_KEY")))

    tools_called = []
    base_executor = make_tool_executor()
    def tracking_executor(action, action_input):
        tools_called.append(action)
        return base_executor(action, action_input)

    agent = ReActAgent(llm=llm, tool_executor=tracking_executor, max_steps=8)
    start = time.time()
    output = agent.run(user_input)
    latency_ms = int((time.time() - start) * 1000)
    return {"output": str(output), "latency_ms": latency_ms,
            "tools_called": tools_called, "steps": len(tools_called), "error": None}


# ───────────────────────────────────────────────
# MOCK RUNNERS
# ───────────────────────────────────────────────

def run_mock_chatbot(user_input):
    time.sleep(0.2)
    return {"output": f"[MOCK CHATBOT] '{user_input[:50]}'",
            "latency_ms": 200, "tools_called": [], "steps": 0, "error": None}

def run_mock_agent(user_input):
    time.sleep(0.3)
    return {"output": f"[MOCK AGENT] '{user_input[:50]}'",
            "latency_ms": 300, "tools_called": ["check_inventory"], "steps": 1, "error": None}


# ───────────────────────────────────────────────
# LOGGER & MAIN
# ───────────────────────────────────────────────

def run_suite(mode: str, use_mock: bool):
    os.makedirs("tests/results", exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    chatbot_results, agent_results = {}, {}

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
                print(f"  🤖 Chatbot ({res['latency_ms']}ms): {str(res['output'])[:80]}")
            except Exception as e:
                chatbot_results[tid] = {"output": str(e), "error": str(e), "latency_ms": 0, "tools_called": [], "steps": 0}
                print(f"  🤖 Chatbot ❌ {e}")

        if mode in ("agent", "both"):
            try:
                res = run_mock_agent(user_input) if use_mock else run_real_agent(user_input)
                agent_results[tid] = res
                expected = set(tc["tools_expected"])
                actual = set(res.get("tools_called", []))
                mark = "✅" if expected.issubset(actual) else "❌"
                print(f"  🧠 Agent  ({res['latency_ms']}ms | tools: {res['tools_called']}) {mark}")
                print(f"           {str(res['output'])[:80]}")
            except Exception as e:
                agent_results[tid] = {"output": str(e), "error": str(e), "latency_ms": 0, "tools_called": [], "steps": 0}
                print(f"  🧠 Agent  ❌ {e}")
        print()

    # Save JSON
    all_results = list(chatbot_results.values()) + list(agent_results.values())
    with open(f"tests/results/run_{ts}.json", "w", encoding="utf-8") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)
    print(f"📁 JSON → tests/results/run_{ts}.json")

    # Save Markdown
    md = [f"# Test Run — {datetime.now().strftime('%Y-%m-%d %H:%M')}\n",
          "| TC | Level | Attack | Chatbot ms | Agent ms | Tools gọi | Pass |",
          "|:---|:------|:-------|:-----------|:---------|:----------|:-----|"]
    for tc in TEST_CASES:
        tid = tc["id"]
        cb = chatbot_results.get(tid, {})
        ag = agent_results.get(tid, {})
        tools = ", ".join(ag.get("tools_called", [])) or "—"
        passed = "✅" if set(tc["tools_expected"]).issubset(set(ag.get("tools_called", []))) else "❌"
        md.append(f"| {tid} | L{tc['level']} | {tc['attack_type']} | {cb.get('latency_ms','?')}ms | {ag.get('latency_ms','?')}ms | {tools} | {passed} |")

    md.append("\n## Chi tiết\n")
    for tc in TEST_CASES:
        tid = tc["id"]
        cb = chatbot_results.get(tid, {})
        ag = agent_results.get(tid, {})
        md += [f"### {tid} — {tc['description']}",
               f"**Input**: `{tc['input']}`",
               f"**Chatbot**: {cb.get('output','N/A')}",
               f"**Agent**: {ag.get('output','N/A')}",
               f"**Tools**: {ag.get('tools_called',[])} | **Bước**: {ag.get('steps','?')}\n---\n"]

    with open(f"tests/results/run_{ts}.md", "w", encoding="utf-8") as f:
        f.write("\n".join(md))
    print(f"📄 Markdown → tests/results/run_{ts}.md")

    # Summary
    if agent_results:
        passed = sum(1 for tc in TEST_CASES
                     if set(tc["tools_expected"]).issubset(set(agent_results.get(tc["id"],{}).get("tools_called",[]))))
        errors = sum(1 for r in agent_results.values() if r.get("error"))
        avg = sum(r["latency_ms"] for r in agent_results.values()) / len(agent_results)
        print(f"\n  Agent: Pass {passed}/{len(TEST_CASES)} | Errors {errors} | Avg {avg:.0f}ms")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["agent", "chatbot", "both"], default="both")
    parser.add_argument("--real", action="store_true")
    args = parser.parse_args()
    run_suite(mode=args.mode, use_mock=not args.real)