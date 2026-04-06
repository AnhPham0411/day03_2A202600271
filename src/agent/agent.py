"""
agent.py  —  Core Agent Builder (Bộ Não)
-----------------------------------------
Nhiệm vụ duy nhất của file này:
  1. Cấu hình System Prompt cho LLM hiểu chu trình ReAct.
  2. Parser: bóc tách Action / Action Input / Final Answer từ output của LLM.
  3. Xử lý lỗi khi LLM trả về sai định dạng.

Không quan tâm tool hoạt động thế nào — việc đó là của Thành viên khác.
"""

import re
from dataclasses import dataclass
from typing import Optional

from src.core.llm_provider import LLMProvider
from src.telemetry.logger import logger


# ---------------------------------------------------------------------------
# Kiểu dữ liệu trả về của Parser
# ---------------------------------------------------------------------------

@dataclass
class ParsedOutput:
    """Kết quả sau khi bóc tách một lượt output từ LLM."""
    thought: Optional[str] = None        # phần lý luận
    action: Optional[str] = None         # tên tool cần gọi
    action_input: Optional[str] = None   # tham số truyền vào tool
    final_answer: Optional[str] = None   # câu trả lời cuối nếu xong
    raw: str = ""                        # output gốc (để log / debug)

    @property
    def is_final(self) -> bool:
        return self.final_answer is not None

    @property
    def is_action(self) -> bool:
        return self.action is not None


# ---------------------------------------------------------------------------
# Mô tả 4 tool thực tế — dùng làm giá trị mặc định khi khởi tạo agent
# Thành viên tool cập nhật nội dung này nếu thêm/bớt tool.
# ---------------------------------------------------------------------------

TOOL_DESCRIPTIONS = """
1. check_inventory
   - Mô tả : Kiểm tra thông tin sản phẩm (giá, tồn kho, danh mục).
   - Input  : product_name (string) — ví dụ: "iphone 15", "macbook air m2"
   - Output : name, price (VND), stock, category | hoặc error nếu không tìm thấy

2. search_product
   - Mô tả : Tìm danh sách sản phẩm theo danh mục.
   - Input  : category (string) — một trong: "dien_thoai", "laptop", "phu_kien"
   - Output : danh sách tên sản phẩm thuộc danh mục đó

3. get_discount
   - Mô tả : Kiểm tra mã giảm giá, trả về tỉ lệ giảm.
   - Input  : coupon_code (string) — ví dụ: "GIAM20", "SALE10", "VOUCHERSAMUNG"
   - Output : discount (0.0 → 1.0, ví dụ 0.2 = giảm 20%)

4. calc_shipping_fee
   - Mô tả : Tính phí vận chuyển theo khoảng cách và khối lượng.
   - Input  : distance_km (số thực, km), weight_kg (số thực, kg) — phân cách bằng dấu phẩy
   - Output : shipping_fee (VND)
   - Công thức: 15.000 + distance_km × 1.000 + weight_kg × 5.000
"""

# ---------------------------------------------------------------------------
# Hằng số định dạng — đổi ở đây nếu muốn thay đổi format toàn bộ agent
# ---------------------------------------------------------------------------

FORMAT_KEYS = {
    "thought":       r"Thought\s*:",
    "action":        r"Action\s*:",
    "action_input":  r"Action\s*Input\s*:",
    "observation":   r"Observation\s*:",
    "final_answer":  r"Final\s*Answer\s*:",
}


# ---------------------------------------------------------------------------
# ReActAgent  —  Bộ Não
# ---------------------------------------------------------------------------

class ReActAgent:
    """
    Điều phối vòng lặp:
        Thought → Action + Action Input → Observation → ... → Final Answer

    Không biết và không cần biết tool hoạt động ra sao.
    Tool executor được inject từ bên ngoài qua tham số `tool_executor`.
    """

    def __init__(
        self,
        llm: LLMProvider,
        tool_executor,                                  # callable(action: str, action_input: str) -> str
        tool_descriptions: str = TOOL_DESCRIPTIONS,    # mặc định dùng 4 tool e-commerce thực tế
        max_steps: int = 8,
    ):
        self.llm = llm
        self.tool_executor = tool_executor
        self.tool_descriptions = tool_descriptions
        self.max_steps = max_steps

    # ------------------------------------------------------------------
    # 1. System Prompt
    # ------------------------------------------------------------------

    def get_system_prompt(self) -> str:
        """
        Hướng dẫn LLM tuân thủ định dạng ReAct nghiêm ngặt.
        Mỗi lượt chỉ được phép xuất ra MỘT trong hai dạng dưới đây.
        """
        return f"""Bạn là trợ lý bán hàng E-commerce thông minh, luôn suy luận từng bước.

    Bạn có thể sử dụng các công cụ (tool) sau:
    {self.tool_descriptions}

    ══════════════════════════════════════════
    ĐỊNH DẠNG BẮT BUỘC — không được lệch dù một ký tự:
    ══════════════════════════════════════════

    Khi cần gọi tool:
    Thought: <lý do bạn cần gọi tool này>
    Action: <tên tool chính xác>
    Action Input: <tham số, nếu nhiều tham số thì phân cách bằng dấu phẩy>

    Khi đã có đủ thông tin để trả lời:
    Thought: <lý do bạn đã đủ thông tin>
    Final Answer: User intent: <một dòng diễn đạt mục đích/ý định người dùng>
    Answer: <câu trả lời ngắn gọn, chính xác — TỐI ĐA 2 câu>

    ══════════════════════════════════════════
    QUY TẮC QUAN TRỌNG:
    ══════════════════════════════════════════
    - Mỗi lượt CHỈ xuất ra một trong hai dạng sau:
      • Thought / Action / Action Input
      • Thought / Final Answer
    - Final Answer phải bắt đầu bằng 'User intent:' rồi 'Answer:'.
    - Final Answer phải ngắn gọn, tối đa 2 câu, không lan man.
    - KHÔNG bịa đặt kết quả tool — chờ Observation từ hệ thống.
    - KHÔNG dùng markdown, JSON, hay code block trong Action Input hay Final Answer.
    - Nếu tool báo lỗi, đọc lỗi trong Observation và thử cách khác.

    Ví dụ 1 — kiểm tra sản phẩm:
    Thought: Tôi cần kiểm tra giá và tồn kho của iPhone 15 trước.
    Action: check_inventory
    Action Input: iphone 15

    Ví dụ 2 — nhiều tham số:
    Thought: Tôi cần tính phí ship, khoảng cách 10km, hàng nặng 0.5kg.
    Action: calc_shipping_fee
    Action Input: 10, 0.5

    Ví dụ 3 — kết thúc (mẫu Final Answer):
    Thought: Tôi đã có đủ thông tin về giá, giảm giá và phí ship.
    Final Answer: User intent: Tính tổng chi phí cho đơn hàng.
    Answer: Tổng chi phí là 21.500.000 VND (đã giảm 20%) + phí ship 25.000 VND.
    """

    # ------------------------------------------------------------------
    # 2. Parser  —  bóc tách output của LLM
    # ------------------------------------------------------------------

    def parse(self, llm_output: str) -> ParsedOutput:
        """
        Bóc tách Thought / Action / Action Input / Final Answer
        từ một chuỗi output thô của LLM.

        Trả về ParsedOutput. Nếu không tìm thấy gì hợp lệ,
        các trường action và final_answer đều là None
        → agent sẽ xử lý như một lỗi định dạng (xem phần 3).
        """
        result = ParsedOutput(raw=llm_output)

        # ── Thought ────────────────────────────────────────────────────────
        thought_match = re.search(
            FORMAT_KEYS["thought"] + r"\s*(.+?)(?=" + "|".join(FORMAT_KEYS.values()) + r"|$)",
            llm_output,
            re.IGNORECASE | re.DOTALL,
        )
        if thought_match:
            result.thought = thought_match.group(1).strip()

        # ── Final Answer (ưu tiên kiểm tra trước Action) ──────────────────
        final_match = re.search(
            FORMAT_KEYS["final_answer"] + r"\s*(.+)",
            llm_output,
            re.IGNORECASE | re.DOTALL,
        )
        if final_match:
            result.final_answer = final_match.group(1).strip()
            return result   # đã có Final Answer, không cần đọc Action nữa

        # ── Action ────────────────────────────────────────────────────────
        action_match = re.search(
            FORMAT_KEYS["action"] + r"\s*(.+?)(?=" + FORMAT_KEYS["action_input"] + r"|$)",
            llm_output,
            re.IGNORECASE | re.DOTALL,
        )
        if action_match:
            result.action = action_match.group(1).strip()

        # ── Action Input ──────────────────────────────────────────────────
        input_match = re.search(
            FORMAT_KEYS["action_input"] + r"\s*(.+?)(?=" + FORMAT_KEYS["observation"] + r"|$)",
            llm_output,
            re.IGNORECASE | re.DOTALL,
        )
        if input_match:
            result.action_input = input_match.group(1).strip()

        return result

    # ------------------------------------------------------------------
    # 3. Xử lý lỗi định dạng
    # ------------------------------------------------------------------

    def _handle_format_error(self, parsed: ParsedOutput, step: int) -> str:
        """
        Trả về Observation hệ thống để nhắc LLM sửa định dạng.
        Không crash, không bỏ qua — buộc LLM thử lại đúng format.
        """
        logger.log_event("FORMAT_ERROR", {"step": step, "raw": parsed.raw})

        if parsed.action and not parsed.action_input:
            # Có Action nhưng thiếu Action Input
            return (
                f"[Lỗi hệ thống] Phát hiện Action '{parsed.action}' "
                "nhưng thiếu dòng 'Action Input:'. "
                "Vui lòng xuất lại đúng định dạng:\n"
                f"Action: {parsed.action}\n"
                "Action Input: <tham số>"
            )

        # Không có Action lẫn Final Answer
        return (
            "[Lỗi hệ thống] Không nhận diện được Action hoặc Final Answer. "
            "Bạn PHẢI xuất ra đúng một trong hai định dạng:\n"
            "  • Thought / Action / Action Input\n"
            "  • Thought / Final Answer"
        )

    def _normalize_final_answer(self, final_answer: str) -> str:
        """
        Chuẩn hóa Final Answer để đảm bảo ngắn gọn và rõ ràng:
        - Nếu LLM tuân thủ mẫu 'User intent: ...\nAnswer: ...', giữ cấu trúc đó.
        - Rút gọn phần Answer còn tối đa 2 câu nếu quá dài.
        - Nếu không có mẫu, trả về tối đa 2 câu đầu của final_answer.
        """
        if not final_answer:
            return final_answer

        # Tìm mẫu 'User intent: ...' và 'Answer: ...'
        m = re.search(r"User intent\s*:\s*(.+?)\s*Answer\s*:\s*(.+)", final_answer, re.IGNORECASE | re.DOTALL)
        if m:
            intent = m.group(1).strip()
            answer = m.group(2).strip()
        else:
            # Không có intent rõ ràng — coi toàn bộ là answer
            intent = None
            answer = final_answer.strip()

        # Rút gọn answer xuống tối đa 2 câu
        sentences = re.split(r'(?<=[.!?])\s+', answer)
        short_answer = " ".join(sentences[:2]).strip()

        if intent:
            return f"User intent: {intent}\nAnswer: {short_answer}"
        return short_answer

    # ------------------------------------------------------------------
    # 4. Vòng lặp chính
    # ------------------------------------------------------------------

    def run(self, user_input: str) -> str:
        logger.log_event("AGENT_START", {
            "input": user_input,
            "model": self.llm.model_name,
            "max_steps": self.max_steps,
        })

        scratchpad = f"Câu hỏi của người dùng: {user_input}\n"
        seen_actions: set = set()
        final_answer: Optional[str] = None
        step = 0

        for step in range(1, self.max_steps + 1):
            logger.log_event("AGENT_STEP", {"step": step})

            # ── Gọi LLM ───────────────────────────────────────────────────
            llm_output = self.llm.generate(
                scratchpad,
                system_prompt=self.get_system_prompt(),
            )
            logger.log_event("LLM_OUTPUT", {"step": step, "output": llm_output})
            scratchpad += f"\n{llm_output.strip()}\n"

            # ── Parser bóc tách ────────────────────────────────────────────
            parsed = self.parse(llm_output)

            # ── Final Answer → kết thúc ────────────────────────────────────
            if parsed.is_final:
                # Chuẩn hóa Final Answer để ngắn gọn, rõ ý định người dùng
                final_answer = self._normalize_final_answer(parsed.final_answer)
                logger.log_event("AGENT_FINAL", {"answer": final_answer, "steps": step})
                break

            # ── Lỗi định dạng → nhắc LLM sửa ─────────────────────────────
            if not parsed.is_action:
                observation = self._handle_format_error(parsed, step)
                scratchpad += f"Observation: {observation}\n"
                continue

            # ── Phát hiện gọi tool lặp (chống vòng lặp vô hạn) ───────────
            action_key = f"{parsed.action}|{parsed.action_input}"
            if action_key in seen_actions:
                observation = (
                    f"[Hệ thống] Bạn đã gọi '{parsed.action}' với input "
                    f"'{parsed.action_input}' rồi. Hãy dùng kết quả cũ "
                    "hoặc chuyển sang bước tiếp theo."
                )
                logger.log_event("DUPLICATE_ACTION", {"action": action_key})
            else:
                seen_actions.add(action_key)
                # ── Gọi tool — do Thành viên khác implement ───────────────
                observation = self.tool_executor(parsed.action, parsed.action_input or "")
                logger.log_event("TOOL_RESULT", {
                    "tool": parsed.action,
                    "input": parsed.action_input,
                    "result": observation,
                })

            scratchpad += f"Observation: {observation}\n"

        else:
            # Hết max_steps mà chưa có Final Answer
            logger.log_event("MAX_STEPS_REACHED", {"max_steps": self.max_steps})
            final_answer = (
                f"Xin lỗi, tôi không thể hoàn thành yêu cầu trong {self.max_steps} bước. "
                "Vui lòng thử diễn đạt lại câu hỏi."
            )

        logger.log_event("AGENT_END", {"total_steps": step})
        return final_answer