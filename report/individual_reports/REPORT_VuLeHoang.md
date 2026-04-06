# Individual Report: Lab 3 - Chatbot vs ReAct Agent

- **Student Name**: Vũ Lê Hoàng
- **Student ID**: 2a202600342
- **Date**: 6/4/2026
---

## I. Technical Contribution (15 Points)

*Describe your specific contribution to the codebase (e.g., implemented a specific tool, fixed the parser, etc.).*

- **Modules Implemented**: `src/agent/agent.py` (Core ReAct agent)
- **Code Highlights**:
	- Chỉnh `get_system_prompt()` để yêu cầu Final Answer theo mẫu `User intent:` và `Answer:`; giới hạn `Answer` tối đa 2 câu.
	- Thêm helper `_normalize_final_answer(final_answer: str) -> str` để rút gọn/chuẩn hoá output của LLM (giữ `User intent` khi có, cắt `Answer` xuống tối đa 2 câu).
	- Tích hợp bước chuẩn hoá vào vòng lặp `run()` trước khi chấp nhận Final Answer.
	- Các thay đổi chính nằm tại `src/agent/agent.py`.
- **Documentation**: Agent giữ `scratchpad` (history) và tuân theo định dạng ReAct; Final Answer được chuẩn hoá để đảm bảo ngắn gọn, rõ ràng và dễ sử dụng cho UI/automation.

---

## II. Debugging Case Study (10 Points)

*Analyze a specific failure event you encountered during the lab using the logging system.*

- **Problem Description**: TC-06 — Agent không phân biệt mã giảm giá "không tồn tại" và "giảm 0%", dẫn tới dừng sớm và không thực hiện `check_inventory`.
- **Log Source**: `logs/2026-04-06.json` (sự kiện `TOOL_RESULT` & `AGENT_STEP` liên quan tới `get_discount`).
- **Diagnosis**: Tool `get_discount` trả `{discount: 0}` cho cả hai trường hợp (mã không tồn tại và mã giảm 0%), nên LLM hiểu đó là kết quả hợp lệ và dừng luồng xử lý.
- **Solution**:
	- Sửa tool `get_discount` để trả `{"error": "Mã không tồn tại"}` khi mã không hợp lệ.
	- Thắt chặt system prompt: nếu user đề cập mã giảm giá thì agent nên luôn gọi `get_discount`.
	- Thêm `_normalize_final_answer()` để đảm bảo agent không dừng sớm do câu trả lời thiếu chuẩn xác.

---

## III. Personal Insights: Chatbot vs ReAct (10 Points)

*Reflect on the reasoning capability difference.*

1.  **Reasoning**: `Thought` giúp LLM biểu diễn chuỗi suy luận rõ ràng (ví dụ: cần gọi tool nào và vì sao), cho phép agent tự động chuỗi nhiều tool calls để hoàn thành nhiệm vụ.
2.  **Reliability**: Agent có thể tốn token nhiều hơn và đôi khi tuân theo câu chữ người dùng (ví dụ "chỉ tính ship") dẫn đến bỏ qua bước logic cần thiết (`get_discount`).
3.  **Observation**: Observation từ tool là feedback quan trọng để LLM điều chỉnh hướng đi (ví dụ: khi `get_discount` trả lỗi, agent sẽ tiếp tục với `check_inventory`).

---

## IV. Future Improvements (5 Points)

*How would you scale this for a production-level AI agent system?*

- **Scalability**: Sử dụng queue bất đồng bộ cho tool calls và caching (Redis) cho kết quả tool thường dùng.
- **Safety**: Sanitize input trước khi gọi tool; thêm supervisor LLM/logic kiểm tra các action nhạy cảm.
- **Performance**: Giảm `max_steps` cho production (5–6), áp token cap (ví dụ 5,000) để tránh runaway chi phí; dùng retrieval/Vector DB cho nhiều tool.

---

> [!NOTE]
> Submit this report by renaming it to `REPORT_[YOUR_NAME].md` and placing it in this folder.
