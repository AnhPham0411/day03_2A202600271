# Individual Report: Lab 3 - Chatbot vs ReAct Agent

- **Student Name**: Hoang Tuan Anh
- **Student ID**: 2A202600075
- **Date**: 2026-04-06
- **Role**: Member 4 — QA & Prompt Attacker

---

## I. Technical Contribution

### Modules implemented

| File | Mô tả |
|:-----|:------|
| `tests/test_cases.md` | 10 test cases chia 3 level, mỗi case có attack type và expected behavior rõ ràng |
| `run_tests.py` | Script tự động chạy song song Chatbot vs Agent, xuất JSON + Markdown report |

### Thiết kế hệ thống test

Script `run_tests.py` được xây dựng theo kiến trúc 2 layer:

```
mock mode  → chạy ngay mà không cần API key (dùng khi dev)
real mode  → kết nối thật với OpenAI + Agent + Tools
```



### Kết quả test run thật (2026-04-06 18:12)

| Chỉ số | Chatbot | Agent |
|:-------|--------:|------:|
| Pass rate | 0/10  | 7/10 |
| Avg latency | 2,756ms | 4,718ms |
| Avg tokens / TC | 147 | 2,460 |
| Tổng chi phí | $0.00073 | $0.00431 |

Nhận xét: chatbot có điểm yếu: không truy cập được dữ liệu thực → không giải được bài toán <br>
Agent có khả năng gọi API (check_inventory, get_discount, calc_shipping_fee), xử lý multi-step reasoning, đưa ra kết quả chính xác
---

## II. Debugging Case Study

### Lỗi được chọn phân tích: TC-09 — "Prompt Attack" — Agent bỏ qua bước get_discount

**Problem Description**

TC-09 yêu cầu: kiểm tra kho → áp mã SALE10 → tính ship. User nhấn mạnh *"Chỉ tính tiền ship thôi"* để đánh lạc hướng. Expected tools: `check_inventory → get_discount → calc_shipping_fee`.

Kết quả thực tế:

```
Tools gọi: ['check_inventory', 'calc_shipping_fee']
Bước: 2  (thiếu 1 bước get_discount)
Output: "Phí ship cho đơn hàng AirPods Pro 2 đến nhà bạn là 67.500 VND."
```

**Trace từ log:**

```
Bước 1
Thought: Tôi cần kiểm tra thông tin tồn kho của AirPods Pro trước.
Action: check_inventory
Action Input: AirPods Pro
Observation: {'name': 'AirPods Pro 2', 'price': 6000000, 'stock': 15}

Bước 2
Thought: AirPods Pro 2 còn hàng. Tôi cần tính phí vận chuyển.
Action: calc_shipping_fee
Action Input: 50, 0.5
Observation: {'shipping_fee': 67500.0}

Final Answer: Phí ship là 67.500 VND.
```

**Diagnosis**

LLM bị ảnh hưởng bởi cụm từ *"Chỉ tính tiền ship thôi"* trong câu hỏi. Khi em dùng từ hạn chế phạm vi, LLM hiểu theo nghĩa đen: chỉ cần tính ship thì không cần kiểm tra mã giảm giá. Về mặt logic bề mặt điều này đúng vì mã SALE10 không liên quan đến phí ship. Nhưng yêu cầu thực sự là *trả lời xem mã đó có ảnh hưởng đến ship không*, và câu trả lời đúng là: mã SALE10 không áp dụng cho phí ship, phí ship vẫn là 67.500 VND.

Em nghĩ đây không phải lỗi kỹ thuật (parser hoạt động tốt, tool hoạt động đúng). Đây là lỗi **reasoning shortcut** — LLM bỏ qua một bước vì từ ngữ user dùng gợi ý không cần thiết.

**Root Cause**: System prompt của Agent v1 không có ví dụ few-shot cho trường hợp user nói "chỉ cần X" nhưng thực ra vẫn cần kiểm tra các bước liên quan.

**Proposed Fix cho Agent v2**

Thêm vào system prompt quy tắc:

```
QUY TẮC: Nếu user đề cập đến mã giảm giá trong câu hỏi, LUÔN gọi
get_discount để kiểm tra, dù user có nói "chỉ tính ship" hay "không
cần tính tiền hàng". Mã giảm giá có thể ảnh hưởng đến các phần khác
của đơn hàng mà user chưa nhận ra.
```

---

## III. Personal Insights: Chatbot vs ReAct 

### 1. Reasoning: Thought block giúp gì so với Chatbot?

Chatbot trả lời TC-08 (full pipeline) như sau:

> *"Mình cần làm rõ một số thông tin: Giá niêm yết iPhone 15 là bao nhiêu? Mức giảm giá GIAM20 là bao nhiêu? Phí ship tính theo km thế nào?"*

Agent không hỏi lại. Nó tự giải quyết từng câu hỏi đó bằng cách gọi tool. Sự khác biệt nằm ở `Thought` block. agent buộc LLM phải viết ra lý do trước khi hành động, giống như bắt một người giải bài toán phải ghi ra từng bước thay vì nhảy thẳng đến đáp án. Khi LLM viết "Tôi cần kiểm tra giá iPhone 15 trước", nó đã tự commit với hành động tiếp theo, giảm khả năng đoán bừa.

### 2. Reliability: Khi nào Agent tệ hơn Chatbot?

TC-06 là ví dụ điển hình. Chatbot trả lời: *"Mã XXXXXXX mã từ đâu của cửa hàng nào."* — câu trả lời này về mặt thực tế là đủ dùng. Agent gọi `get_discount("XXXXXXX")` → nhận `{discount: 0}` → rồi dừng lại, không tiếp tục kiểm tra kho, và kết luận luôn là mã giảm giá không tồn tại. Agent tốn 1,647 tokens và $0.00028 để đưa ra kết quả kém chắc chắn hơn Chatbot tốn 132 tokens.

**Bài học**: Agent tốn kém hơn và không nhất thiết chính xác hơn trong các câu hỏi 1-step đơn giản. Agent thắng ở multi-step, thua ở simplicity.

### 3. Observation: Feedback ảnh hưởng thế nào đến bước tiếp theo?

TC-07 là ví dụ rõ nhất. Sau khi `check_inventory("Samsung Galaxy Z Fold 6")` trả về `{'error': 'Product not found'}`, agent không bịa ra giá mà chuyển sang gọi `search_product("dien_thoai")` để tìm xem kho có gì tương tự. Đây là behavior đúng. Observation từ tool thất bại đã trigger một chiến lược fallback thông minh. Chatbot trong cùng tình huống lại bịa ra cả quy trình đặt hàng 4 bước cho một sản phẩm không tồn tại.

---

## IV. Future Improvements

### Scalability: Async tool calls

Hiện tại agent gọi tool tuần tự: `check_inventory` xong rồi mới `get_discount`. Với đơn hàng phức tạp hơn, 2 tool này độc lập nhau hoàn toàn — có thể gọi song song bằng `asyncio.gather()`, giảm latency xuống còn ~60% so với sequential.

### Safety: Supervisor LLM

Thêm một LLM phụ đóng vai "giám sát" — trước khi agent thực hiện action nào có tiền sử lỗi cao (ví dụ: `calc_shipping_fee` với input không phải số), supervisor kiểm tra tính hợp lệ của tham số. Tương tự guardrail trong sản xuất thực tế.

### Performance: Tool retrieval bằng Vector DB

Khi số tool tăng lên 20-30 cái, nhét hết vào system prompt sẽ tốn hàng nghìn tokens mỗi request. Giải pháp: dùng vector embedding để tìm top-3 tool phù hợp nhất với câu hỏi, chỉ đưa 3 tool đó vào prompt. Token giảm 80%, latency giảm đáng kể.

---

