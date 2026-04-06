import os
from dotenv import load_dotenv
from src.core.openai_provider import OpenAIProvider


SYSTEM_PROMPT = (
    "Bạn là trợ lý bán hàng E-commerce thân thiện. "
    "Nhiệm vụ của bạn là tư vấn sản phẩm và chốt đơn ngắn gọn, rõ ràng. "
    "Nếu thiếu dữ liệu giá/khuyến mãi/ship để tính chính xác, hãy nói rõ giới hạn và đề nghị người dùng cung cấp thêm thông tin. "
    "Không được tự ý khẳng định đã kiểm tra kho, mã giảm giá hay phí ship theo hệ thống nội bộ."
)


def build_chatbot() -> OpenAIProvider:
    load_dotenv()

    model_name = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
    api_key = os.getenv("OPENAI_API_KEY")

    if not api_key:
        raise ValueError(
            "Không tìm thấy API key. Hãy đặt OPENROUTER_API_KEY (hoặc OPENAI_API_KEY) trong file .env"
        )

    return OpenAIProvider(model_name=model_name, api_key=api_key)


def chat_loop() -> None:
    chatbot = build_chatbot()

    print("=== Baseline Chatbot (No Tools) ===")
    print(f"Model: {chatbot.model_name}")
    print("Nhập 'exit' để thoát.\n")

    while True:
        user_input = input("Bạn: ").strip()
        if user_input.lower() in {"exit", "quit", "q"}:
            print("Tạm biệt!")
            break
        if not user_input:
            continue

        try:
            result = chatbot.generate(prompt=user_input, system_prompt=SYSTEM_PROMPT)
            print(f"Bot: {result['content']}")

            usage = result.get("usage", {})
            latency_ms = result.get("latency_ms", "?")
            print(
                "[meta] "
                f"latency={latency_ms}ms, "
                f"prompt={usage.get('prompt_tokens', '?')}, "
                f"completion={usage.get('completion_tokens', '?')}, "
                f"total={usage.get('total_tokens', '?')}\n"
            )
        except Exception as error:
            print(f"Bot lỗi: {error}\n")


if __name__ == "__main__":
    chat_loop()
