import json
import os
from typing import Dict, Any, List
# Import logger từ folder telemetry
from src.telemetry.logger import logger

class PerformanceTracker:
    def __init__(self):
        self.session_metrics = []
        # Bảng giá cập nhật 2024/2025 (đơn giá trên 1M tokens)
        self.PRICING = {
            "gpt-4o": {"input": 5.0, "output": 15.0},
            "gpt-3.5-turbo": {"input": 0.5, "output": 1.5},
            "gemini-1.5-flash": {"input": 0.075, "output": 0.3},
            "default": {"input": 1.0, "output": 2.0}
        }

    def track_request(self, provider: str = "Unknown", model: str = "Unknown", usage: Dict[str, int] = None, latency_ms: int = 0, steps: int = 1):
        """Ghi lại thông số một request với cơ chế phòng thủ lỗi."""
        try:
            if not isinstance(usage, dict):
                usage = {}
            
            prompt_tokens = int(usage.get("prompt_tokens", 0))
            completion_tokens = int(usage.get("completion_tokens", 0))
            total_tokens = prompt_tokens + completion_tokens
            
            cost = self._calculate_cost(model, prompt_tokens, completion_tokens)
            tps = (completion_tokens / (latency_ms / 1000)) if latency_ms > 0 else 0

            metric = {
                "provider": str(provider),
                "model": str(model),
                "tokens": {
                    "prompt": prompt_tokens,
                    "completion": completion_tokens,
                    "total": total_tokens
                },
                "latency_ms": int(latency_ms),
                "tokens_per_sec": round(float(tps), 2),
                "cost_usd": round(float(cost), 6),
                "inference_steps": int(steps)
            }
            
            self.session_metrics.append(metric)
            logger.log_event("LLM_METRIC", metric)

        except Exception as e:
            print(f"⚠️ [Telemetry Warning] Lỗi ghi nhận metric: {e}")

    def _calculate_cost(self, model: str, input_tokens: int, output_tokens: int) -> float:
        model_key = model.lower()
        pricing = self.PRICING.get(next((k for k in self.PRICING if k in model_key), "default"))
        return (input_tokens / 1_000_000 * pricing["input"]) + (output_tokens / 1_000_000 * pricing["output"])

    @staticmethod
    def generate_report(log_file_path: str):
        """
        Hàm dành riêng cho Task 5: Đọc file log và tính P50, P99 cho Dashboard.
        """
        if not os.path.exists(log_file_path):
            print(f"❌ Lỗi: Không tìm thấy file log tại {log_file_path}")
            return

        latencies = []
        tokens_list = []
        total_cost = 0.0
        total_requests = 0

        with open(log_file_path, 'r', encoding='utf-8') as f:
            for line in f:
                try:
                    log_entry = json.loads(line)
                    if log_entry.get("event") == "LLM_METRIC":
                        data = log_entry["data"]
                        latencies.append(data.get("latency_ms", 0))
                        tokens_list.append(data.get("tokens", {}).get("total", 0))
                        total_cost += data.get("cost_usd", 0)
                        total_requests += 1
                except:
                    continue

        if total_requests == 0:
            print("📭 Không tìm thấy dữ liệu LLM_METRIC nào để báo cáo.")
            return

        # Tính toán P50 và P99
        latencies.sort()
        p50 = latencies[int(len(latencies) * 0.5)]
        p99 = latencies[int(len(latencies) * 0.99)] if len(latencies) > 1 else latencies[0]
        avg_tokens = sum(tokens_list) / total_requests

        # In ra Dashboard cực chuyên nghiệp
        print("\n" + "=".center(50, "="))
        print("📊 SMART E-COMMERCE AGENT PERFORMANCE".center(50))
        print("-".center(50, "-"))
        print(f"🔹 Average Latency (P50):  {p50} ms")
        print(f"🔹 Max Latency (P99):      {p99} ms")
        print(f"🔹 Avg Tokens per Task:    {int(avg_tokens)} tokens")
        print(f"🔹 Total Cost of Suite:    ${total_cost:.5f}")
        print(f"🔹 Total Requests Run:     {total_requests}")
        print("=".center(50, "=") + "\n")

# Global tracker instance
tracker = PerformanceTracker()