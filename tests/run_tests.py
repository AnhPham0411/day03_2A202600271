"""
run_tests.py — QA Test Runner (Member 4)
Branch: feature/qa-testing

Cách dùng:
    python run_tests.py --mode both        # mock (không cần API key)
    python run_tests.py --mode both --real # thật (cần OPENAI_API_KEY trong .env)

Metrics thu thập (theo EVALUATION.md):
    1. Token Efficiency  — prompt / completion / total + tỉ lệ + cost ước tính
    2. Latency           — tổng thời gian mỗi lần chạy (ms)
    3. Loop count        — số bước Thought→Action + termination quality
    4. Failure Analysis  — phân loại lỗi: JSON_PARSE / HALLUCINATION / TIMEOUT / TOOL_ERROR
"""

import os
import sys
import json
import time
import argparse
import re
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
# COST CONFIG  (USD per 1M tokens — gpt-4o-mini)
# ───────────────────────────────────────────────

COST_PER_1M = {
    "prompt":     0.150,   # $0.150 / 1M input tokens
    "completion": 0.600,   # $0.600 / 1M output tokens
}

KNOWN_TOOLS = {"check_inventory", "get_discount", "calc_shipping_fee", "search_product"}

# ───────────────────────────────────────────────
# TOKEN HELPERS
# ───────────────────────────────────────────────

ZERO_TOKENS = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}


def extract_tokens_from_response(response: dict) -> dict:
    """Trích token usage từ response OpenAI hoặc Anthropic."""
    usage = response.get("usage") or {}
    if "prompt_tokens" in usage:
        return {
            "prompt_tokens":     usage.get("prompt_tokens", 0),
            "completion_tokens": usage.get("completion_tokens", 0),
            "total_tokens":      usage.get("total_tokens", 0),
        }
    if "input_tokens" in usage:
        p = usage.get("input_tokens", 0)
        c = usage.get("output_tokens", 0)
        return {"prompt_tokens": p, "completion_tokens": c, "total_tokens": p + c}
    return dict(ZERO_TOKENS)


def accumulate_tokens(base: dict, extra: dict) -> dict:
    return {k: base[k] + extra[k] for k in ZERO_TOKENS}


def calc_cost(tokens: dict) -> float:
    """Tính chi phí ước tính USD theo giá gpt-4o-mini."""
    cost = (tokens["prompt_tokens"]     / 1_000_000) * COST_PER_1M["prompt"]
    cost += (tokens["completion_tokens"] / 1_000_000) * COST_PER_1M["completion"]
    return round(cost, 6)


def token_efficiency_ratio(tokens: dict) -> str:
    """
    Tỉ lệ completion/prompt.
    Thấp  → prompt quá dài / agent ít sinh text  (tốt về chi phí)
    Cao   → agent sinh nhiều text so với prompt   (có thể 'chatter')
    """
    if tokens["prompt_tokens"] == 0:
        return "N/A"
    ratio = tokens["completion_tokens"] / tokens["prompt_tokens"]
    return f"{ratio:.2f}"


def _fmt_tokens(tokens: dict | None) -> str:
    """Dạng ngắn cho bảng: vào↑ ra↓ (tổng)"""
    if not tokens or tokens.get("total_tokens", 0) == 0:
        return "—"
    return (f"{tokens['prompt_tokens']}↑ "
            f"{tokens['completion_tokens']}↓ "
            f"(tổng {tokens['total_tokens']})")


# ───────────────────────────────────────────────
# FAILURE ANALYSIS HELPERS
# ───────────────────────────────────────────────

class FailureCode:
    OK            = "OK"
    JSON_PARSE    = "JSON_PARSE_ERROR"     # LLM output sai format JSON/Action
    HALLUCINATION = "HALLUCINATION_ERROR"  # Gọi tool không tồn tại
    TIMEOUT       = "TIMEOUT"              # Vượt max_steps
    TOOL_ERROR    = "TOOL_ERROR"           # Tool trả về lỗi runtime
    EXCEPTION     = "EXCEPTION"            # Lỗi Python không mong đợi


def classify_failure(trace: list, output: str, error: str | None,
                     steps: int, max_steps: int = 8) -> dict:
    """
    Phân tích trace + output để xác định loại lỗi.
    Trả về {"code": FailureCode, "detail": "mô tả ngắn"}
    """
    if error:
        return {"code": FailureCode.EXCEPTION, "detail": str(error)[:120]}

    # Timeout: dùng hết max_steps mà không có Final Answer
    if steps >= max_steps and "final answer" not in output.lower():
        return {"code": FailureCode.TIMEOUT,
                "detail": f"Đạt max_steps={max_steps}, không kết thúc bằng Final Answer"}

    for step in trace:
        obs = step.get("observation", "")
        action = step.get("action", "")

        # Hallucination: gọi tool ngoài danh sách
        if action and action.lower() not in KNOWN_TOOLS:
            return {"code": FailureCode.HALLUCINATION,
                    "detail": f"Gọi tool không tồn tại: '{action}'"}

        # Tool error: observation chứa dấu hiệu lỗi
        if obs.startswith("[Lỗi") or obs.startswith("[ERROR"):
            if "không tồn tại" in obs:
                return {"code": FailureCode.HALLUCINATION,
                        "detail": f"Tool không tồn tại trong observation: {obs[:80]}"}
            return {"code": FailureCode.TOOL_ERROR,
                    "detail": obs[:120]}

        # JSON parse error: thought chứa dấu hiệu parse fail
        thought = step.get("thought", "").lower()
        if any(kw in thought for kw in ["invalid json", "parse error", "json error",
                                         "```json", "could not parse"]):
            return {"code": FailureCode.JSON_PARSE,
                    "detail": step.get("thought", "")[:120]}

    return {"code": FailureCode.OK, "detail": ""}


def termination_quality(output: str, steps: int, max_steps: int = 8) -> str:
    """
    Đánh giá chất lượng kết thúc của agent.
    CLEAN   → kết thúc bằng Final Answer đúng lúc
    TIMEOUT → hết max_steps
    EMPTY   → output rỗng
    """
    if not output or output.strip() == "":
        return "EMPTY"
    if steps >= max_steps:
        return "TIMEOUT"
    if "final answer" in output.lower() or len(output) > 20:
        return "CLEAN"
    return "UNKNOWN"


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
    tokens = extract_tokens_from_response(result if isinstance(result, dict) else {})

    return {
        "output":       str(output),
        "latency_ms":   latency_ms,
        "tools_called": [],
        "steps":        0,
        "tokens":       tokens,
        "cost_usd":     calc_cost(tokens),
        "trace":        [],
        "failure":      {"code": FailureCode.OK, "detail": ""},
        "termination":  "CLEAN",
        "error":        None,
    }


def run_real_agent(user_input: str) -> dict:
    from src.agent.agent import ReActAgent
    from src.core.openai_provider import OpenAIProvider

    MAX_STEPS = 8
    accumulated_tokens = dict(ZERO_TOKENS)
    _last_llm_output   = {"text": ""}

    class TrackingProvider:
        def __init__(self, p):
            self._p = p
            self.model_name = p.model_name

        def generate(self, prompt, system_prompt=None):
            nonlocal accumulated_tokens
            raw = self._p.generate(prompt, system_prompt=system_prompt)
            step_tokens = extract_tokens_from_response(raw if isinstance(raw, dict) else {})
            accumulated_tokens = accumulate_tokens(accumulated_tokens, step_tokens)
            text = raw.get("content", "") if isinstance(raw, dict) else raw
            _last_llm_output["text"] = str(text)
            return text

    llm = TrackingProvider(
        OpenAIProvider(model_name="gpt-4o-mini", api_key=os.getenv("OPENAI_API_KEY"))
    )

    tools_called  = []
    trace         = []
    base_executor = make_tool_executor()

    def tracking_executor(action, action_input):
        observation = base_executor(action, action_input)
        tools_called.append(action)

        raw_text = _last_llm_output.get("text", "")
        thought_line = ""
        for line in raw_text.splitlines():
            stripped = line.strip()
            if stripped.lower().startswith("thought"):
                thought_line = stripped
                break
        if not thought_line:
            thought_line = raw_text[:150].strip()

        trace.append({
            "step":         len(trace) + 1,
            "thought":      thought_line,
            "action":       action,
            "action_input": action_input,
            "observation":  str(observation),
        })
        return observation

    agent = ReActAgent(llm=llm, tool_executor=tracking_executor, max_steps=MAX_STEPS)
    start = time.time()
    output = agent.run(user_input)
    latency_ms = int((time.time() - start) * 1000)

    steps      = len(tools_called)
    failure    = classify_failure(trace, output, None, steps, MAX_STEPS)
    termination = termination_quality(output, steps, MAX_STEPS)

    return {
        "output":       str(output),
        "latency_ms":   latency_ms,
        "tools_called": tools_called,
        "steps":        steps,
        "tokens":       accumulated_tokens,
        "cost_usd":     calc_cost(accumulated_tokens),
        "trace":        trace,
        "failure":      failure,
        "termination":  termination,
        "error":        None,
    }


# ───────────────────────────────────────────────
# MOCK RUNNERS
# ───────────────────────────────────────────────

def run_mock_chatbot(user_input: str) -> dict:
    time.sleep(0.2)
    tokens = {"prompt_tokens": 120, "completion_tokens": 80, "total_tokens": 200}
    return {
        "output":       f"[MOCK CHATBOT] '{user_input[:50]}'",
        "latency_ms":   200,
        "tools_called": [],
        "steps":        0,
        "tokens":       tokens,
        "cost_usd":     calc_cost(tokens),
        "trace":        [],
        "failure":      {"code": FailureCode.OK, "detail": ""},
        "termination":  "CLEAN",
        "error":        None,
    }


def run_mock_agent(user_input: str) -> dict:
    time.sleep(0.3)
    tokens = {"prompt_tokens": 300, "completion_tokens": 150, "total_tokens": 450}
    trace = [
        {
            "step":         1,
            "thought":      "Thought: [MOCK] Cần kiểm tra tồn kho sản phẩm trước.",
            "action":       "check_inventory",
            "action_input": user_input[:40],
            "observation":  "[MOCK] Còn hàng: 10 sản phẩm",
        }
    ]
    failure = classify_failure(trace, "[MOCK] Final Answer: có hàng", None, 1)
    return {
        "output":       f"[MOCK AGENT] '{user_input[:50]}'",
        "latency_ms":   300,
        "tools_called": ["check_inventory"],
        "steps":        1,
        "tokens":       tokens,
        "cost_usd":     calc_cost(tokens),
        "trace":        trace,
        "failure":      failure,
        "termination":  "CLEAN",
        "error":        None,
    }


# ───────────────────────────────────────────────
# FAILURE ICON HELPER
# ───────────────────────────────────────────────

def _failure_icon(failure: dict) -> str:
    code = failure.get("code", FailureCode.OK)
    icons = {
        FailureCode.OK:            "OK",
        FailureCode.JSON_PARSE:    "JSON_PARSE",
        FailureCode.HALLUCINATION: "HALLUCINATION",
        FailureCode.TIMEOUT:       "TIMEOUT",
        FailureCode.TOOL_ERROR:    "TOOL_ERROR",
        FailureCode.EXCEPTION:     "EXCEPTION",
    }
    return icons.get(code, code)


# ───────────────────────────────────────────────
# MAIN SUITE
# ───────────────────────────────────────────────

def run_suite(mode: str, use_mock: bool):
    os.makedirs("tests/results", exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    chatbot_results: dict = {}
    agent_results:   dict = {}

    print(f"\n{'='*65}")
    print(f"  QA TEST SUITE — {'MOCK' if use_mock else 'REAL'} | {mode.upper()}")
    print(f"{'='*65}\n")

    for tc in TEST_CASES:
        tid, user_input = tc["id"], tc["input"]
        print(f"[{tid}] L{tc['level']} | {tc['attack_type']}")
        print(f"  ➜ {user_input[:75]}")

        # ── Chatbot ───────────────────────────
        if mode in ("chatbot", "both"):
            try:
                res = run_mock_chatbot(user_input) if use_mock else run_real_chatbot(user_input)
                chatbot_results[tid] = res
                tok = res["tokens"]
                print(
                    f"  🤖 Chatbot  {res['latency_ms']}ms | "
                    f"tokens tổng={tok['total_tokens']} | "
                    f"cost=${res['cost_usd']:.5f} | "
                    f"{_failure_icon(res['failure'])}"
                )
                print(f"             {str(res['output'])[:80]}")
            except Exception as e:
                chatbot_results[tid] = {
                    "output": str(e), "error": str(e), "latency_ms": 0,
                    "tools_called": [], "steps": 0, "tokens": dict(ZERO_TOKENS),
                    "cost_usd": 0.0, "trace": [],
                    "failure": {"code": FailureCode.EXCEPTION, "detail": str(e)},
                    "termination": "EMPTY",
                }
                print(f"  🤖 Chatbot ❌ {e}")

        # ── Agent ─────────────────────────────
        if mode in ("agent", "both"):
            try:
                res = run_mock_agent(user_input) if use_mock else run_real_agent(user_input)
                agent_results[tid] = res
                expected = set(tc["tools_expected"])
                actual   = set(res.get("tools_called", []))
                pass_mark = "✅" if expected.issubset(actual) else "❌"
                tok = res["tokens"]
                ratio = token_efficiency_ratio(tok)

                print(
                    f"  🧠 Agent    {res['latency_ms']}ms | "
                    f"bước={res['steps']} | "
                    f"kết thúc={res['termination']} | "
                    f"tokens tổng={tok['total_tokens']} | "
                    f"tỉ lệ={ratio} | "
                    f"cost=${res['cost_usd']:.5f} | "
                    f"{_failure_icon(res['failure'])} | {pass_mark}"
                )
                print(f"             {str(res['output'])[:80]}")

                # In trace
                for step in res.get("trace", []):
                    print(f"  │ Bước {step['step']}")
                    print(f"  │  💭 Thought    : {step['thought'][:100]}")
                    print(f"  │  ⚡ Action     : {step['action']}({step['action_input'][:60]})")
                    print(f"  │  👁  Observation: {step['observation'][:100]}")

                # In chi tiết lỗi nếu có
                if res["failure"]["code"] != FailureCode.OK:
                    print(f"  ⚠️  Lỗi [{res['failure']['code']}]: {res['failure']['detail'][:100]}")

            except Exception as e:
                agent_results[tid] = {
                    "output": str(e), "error": str(e), "latency_ms": 0,
                    "tools_called": [], "steps": 0, "tokens": dict(ZERO_TOKENS),
                    "cost_usd": 0.0, "trace": [],
                    "failure": {"code": FailureCode.EXCEPTION, "detail": str(e)},
                    "termination": "EMPTY",
                }
                print(f"  🧠 Agent  ❌ {e}")
        print()

    # ────────────────────────────────────────────
    # SAVE JSON
    # ────────────────────────────────────────────
    all_results = list(chatbot_results.values()) + list(agent_results.values())
    json_path = f"tests/results/run_{ts}.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)
    print(f"📁 JSON → {json_path}")

    # ────────────────────────────────────────────
    # BUILD MARKDOWN
    # ────────────────────────────────────────────

    def total_field(results: dict, field: str) -> int:
        return sum(r.get("tokens", {}).get(field, 0) for r in results.values())

    def total_cost(results: dict) -> float:
        return sum(r.get("cost_usd", 0.0) for r in results.values())

    cb_prompt = total_field(chatbot_results, "prompt_tokens")
    cb_comp   = total_field(chatbot_results, "completion_tokens")
    cb_total  = total_field(chatbot_results, "total_tokens")
    cb_cost   = total_cost(chatbot_results)

    ag_prompt = total_field(agent_results, "prompt_tokens")
    ag_comp   = total_field(agent_results, "completion_tokens")
    ag_total  = total_field(agent_results, "total_tokens")
    ag_cost   = total_cost(agent_results)

    n_cb = len(chatbot_results) or 1
    n_ag = len(agent_results)   or 1

    md = []

    # Header
    md += [
        f"# Test Run — {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"> Mode: **{'MOCK' if use_mock else 'REAL'}** | Model: gpt-4o-mini",
        f"> Token format: `vào↑ ra↓ (tổng)` | Tỉ lệ = completion/prompt",
        "",
    ]

    # ── Bảng tổng quan ──
    md += [
        "## 1. Bảng tổng quan kết quả",
        "",
        "| TC | Level | Attack | CB ms | CB Tokens | CB Cost | "
        "AG ms | AG Tokens | AG Cost | AG Tỉ lệ | Bước | Kết thúc | Lỗi | Pass |",
        "|:---|:------|:-------|------:|:----------|--------:|"
        "------:|:----------|--------:|---------:|-----:|:---------|:----|:----:|",
    ]

    for tc in TEST_CASES:
        tid = tc["id"]
        cb  = chatbot_results.get(tid, {})
        ag  = agent_results.get(tid, {})

        cb_tok_fmt = _fmt_tokens(cb.get("tokens"))
        ag_tok_fmt = _fmt_tokens(ag.get("tokens"))
        ag_tok     = ag.get("tokens", ZERO_TOKENS)
        ratio      = token_efficiency_ratio(ag_tok)
        tools      = ", ".join(ag.get("tools_called", [])) or "—"
        passed     = "✅" if set(tc["tools_expected"]).issubset(set(ag.get("tools_called", []))) else "❌"
        failure_md = ag.get("failure", {}).get("code", "—")
        term       = ag.get("termination", "—")
        cb_c       = f"${cb.get('cost_usd', 0):.5f}"
        ag_c       = f"${ag.get('cost_usd', 0):.5f}"

        md.append(
            f"| {tid} | L{tc['level']} | {tc['attack_type']} "
            f"| {cb.get('latency_ms','?')}ms | {cb_tok_fmt} | {cb_c} "
            f"| {ag.get('latency_ms','?')}ms | {ag_tok_fmt} | {ag_c} "
            f"| {ratio} | {ag.get('steps','?')} | {term} | {failure_md} | {passed} |"
        )

    # ── Token & Cost summary ──
    cb_ratio_all = f"{cb_comp/cb_prompt:.2f}" if cb_prompt else "N/A"
    ag_ratio_all = f"{ag_comp/ag_prompt:.2f}" if ag_prompt else "N/A"

    md += [
        "",
        "## 2. Tổng hợp Token & Chi phí",
        "",
        "| Chỉ số | Chatbot | Agent |",
        "|:-------|--------:|------:|",
        f"| Prompt tokens (tổng)       | {cb_prompt:,}         | {ag_prompt:,}         |",
        f"| Completion tokens (tổng)   | {cb_comp:,}           | {ag_comp:,}           |",
        f"| **Total tokens (tổng)**    | **{cb_total:,}**      | **{ag_total:,}**      |",
        f"| Trung bình / test case     | {cb_total//n_cb:,}    | {ag_total//n_ag:,}    |",
        f"| Tỉ lệ completion/prompt    | {cb_ratio_all}        | {ag_ratio_all}        |",
        f"| **Chi phí ước tính (USD)** | **${cb_cost:.5f}**    | **${ag_cost:.5f}**    |",
        f"| Chi phí trung bình / TC    | ${cb_cost/n_cb:.5f}   | ${ag_cost/n_ag:.5f}   |",
        "",
        f"> 💡 Agent dùng nhiều hơn chatbot **{ag_total - cb_total:,} tokens** "
        f"(+{(ag_total/cb_total - 1)*100:.0f}%) do multi-step reasoning."
        if cb_total > 0 else "",
        "",
    ]

    # ── Failure Analysis ──
    failure_counts = {}
    for r in agent_results.values():
        code = r.get("failure", {}).get("code", FailureCode.OK)
        failure_counts[code] = failure_counts.get(code, 0) + 1

    md += [
        "## 3. Phân tích lỗi (Failure Analysis)",
        "",
        "| Loại lỗi | Số lần | Mô tả |",
        "|:---------|-------:|:------|",
        f"| ✅ OK              | {failure_counts.get(FailureCode.OK, 0)} | Hoàn thành đúng |",
        f"| 🔴 JSON_PARSE      | {failure_counts.get(FailureCode.JSON_PARSE, 0)} | LLM output sai format, parser không đọc được |",
        f"| 🟡 HALLUCINATION   | {failure_counts.get(FailureCode.HALLUCINATION, 0)} | Gọi tool không tồn tại trong danh sách |",
        f"| 🟠 TIMEOUT         | {failure_counts.get(FailureCode.TIMEOUT, 0)} | Vượt max_steps, không ra Final Answer |",
        f"| 🔵 TOOL_ERROR      | {failure_counts.get(FailureCode.TOOL_ERROR, 0)} | Tool trả về lỗi runtime |",
        f"| ⚫ EXCEPTION       | {failure_counts.get(FailureCode.EXCEPTION, 0)} | Lỗi Python không mong đợi |",
        "",
    ]

    # Chi tiết từng lỗi
    errors_found = [
        (tid, r) for tid, r in agent_results.items()
        if r.get("failure", {}).get("code", FailureCode.OK) != FailureCode.OK
    ]
    if errors_found:
        md.append("### Chi tiết các trường hợp lỗi\n")
        for tid, r in errors_found:
            f = r["failure"]
            md += [
                f"**{tid}** — `{f['code']}`",
                f"> {f['detail']}",
                "",
            ]

    # ── Termination Quality ──
    term_counts = {}
    for r in agent_results.values():
        t = r.get("termination", "UNKNOWN")
        term_counts[t] = term_counts.get(t, 0) + 1

    md += [
        "## 4. Chất lượng kết thúc (Termination Quality)",
        "",
        "| Trạng thái | Số lần | Ý nghĩa |",
        "|:-----------|-------:|:--------|",
        f"| CLEAN   | {term_counts.get('CLEAN', 0)} | Kết thúc đúng bằng Final Answer |",
        f"| TIMEOUT | {term_counts.get('TIMEOUT', 0)} | Hết max_steps, loop không dừng |",
        f"| EMPTY   | {term_counts.get('EMPTY', 0)} | Output rỗng |",
        f"| UNKNOWN | {term_counts.get('UNKNOWN', 0)} | Không xác định |",
        "",
    ]

    # ── Chi tiết từng TC ──
    md += ["## 5. Chi tiết từng test case", ""]

    for tc in TEST_CASES:
        tid = tc["id"]
        cb  = chatbot_results.get(tid, {})
        ag  = agent_results.get(tid, {})
        cb_tok_raw = cb.get("tokens", ZERO_TOKENS)
        ag_tok_raw = ag.get("tokens", ZERO_TOKENS)
        failure    = ag.get("failure", {"code": FailureCode.OK, "detail": ""})
        term       = ag.get("termination", "—")

        md += [
            f"### {tid} — {tc['description']}",
            f"- **Input**: `{tc['input']}`",
            f"- **Level**: L{tc['level']} | **Attack type**: {tc['attack_type']}",
            "",
            "**🤖 Chatbot**",
            f"- Output: {cb.get('output', 'N/A')}",
            f"- Latency: {cb.get('latency_ms', '?')}ms",
            f"- Tokens: vào={cb_tok_raw.get('prompt_tokens',0)} | "
            f"ra={cb_tok_raw.get('completion_tokens',0)} | "
            f"tổng={cb_tok_raw.get('total_tokens',0)}",
            f"- Chi phí: ${cb.get('cost_usd', 0):.5f}",
            "",
            "**🧠 Agent**",
            f"- Output: {ag.get('output', 'N/A')}",
            f"- Latency: {ag.get('latency_ms', '?')}ms | Bước: {ag.get('steps', '?')} | Kết thúc: {term}",
            f"- Tools gọi: {ag.get('tools_called', [])}",
            f"- Tokens: vào={ag_tok_raw.get('prompt_tokens',0)} | "
            f"ra={ag_tok_raw.get('completion_tokens',0)} | "
            f"tổng={ag_tok_raw.get('total_tokens',0)} | "
            f"tỉ lệ={token_efficiency_ratio(ag_tok_raw)}",
            f"- Chi phí: ${ag.get('cost_usd', 0):.5f}",
            f"- Kết quả lỗi: **{failure['code']}**"
            + (f" — {failure['detail']}" if failure['detail'] else ""),
            "",
            "**📋 Trace (Thought → Action → Observation)**",
        ]

        if ag.get("trace"):
            for step in ag["trace"]:
                md += [
                    f"",
                    f"**Bước {step['step']}**",
                    f"- 💭 Thought: {step['thought']}",
                    f"- ⚡ Action: `{step['action']}` — input: `{step['action_input']}`",
                    f"- 👁 Observation: {step['observation']}",
                ]
        else:
            md.append("_Không có trace (chatbot-only hoặc mock)_")

        md.append("\n---\n")

    # ── Ghi file ──
    md_path = f"tests/results/run_{ts}.md"
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(md))
    print(f"📄 Markdown → {md_path}")

    # ── Console summary ───────────────────────
    if agent_results:
        passed_cnt = sum(
            1 for tc in TEST_CASES
            if set(tc["tools_expected"]).issubset(
                set(agent_results.get(tc["id"], {}).get("tools_called", []))
            )
        )
        error_cnt  = sum(1 for r in agent_results.values()
                         if r.get("failure", {}).get("code") != FailureCode.OK)
        avg_ms     = sum(r["latency_ms"] for r in agent_results.values()) / n_ag
        avg_steps  = sum(r["steps"] for r in agent_results.values()) / n_ag

        print(f"\n{'='*65}")
        print(f"  TỔNG KẾT")
        print(f"{'='*65}")
        print(f"  Agent  : Pass {passed_cnt}/{len(TEST_CASES)} | "
              f"Lỗi {error_cnt} | Avg latency {avg_ms:.0f}ms | Avg bước {avg_steps:.1f}")
        print(f"  Tokens : CB tổng={cb_total:,} (${cb_cost:.5f}) | "
              f"AG tổng={ag_total:,} (${ag_cost:.5f})")
        print(f"  Tỉ lệ  : CB={cb_ratio_all} | AG={ag_ratio_all}  "
              f"(completion/prompt, thấp = prompt hiệu quả hơn)")
        print(f"  Lỗi    : { {k:v for k,v in failure_counts.items() if v>0} }")
        print(f"{'='*65}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["agent", "chatbot", "both"], default="both")
    parser.add_argument("--real", action="store_true")
    args = parser.parse_args()
    run_suite(mode=args.mode, use_mock=not args.real)