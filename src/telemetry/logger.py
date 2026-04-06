import logging
import json
import os
from datetime import datetime
from typing import Any, Dict

class IndustryLogger:
    def __init__(self, name: str = "AI-Lab-Agent", log_dir: str = "logs"):
        self.logger = logging.getLogger(name)
        self.logger.setLevel(logging.INFO)
        
        if not os.path.exists(log_dir):
            os.makedirs(log_dir)

        # File Handler: Đặt tên theo ngày để dễ quản lý
        log_file = os.path.join(log_dir, f"{datetime.now().strftime('%Y-%m-%d')}.json")
        file_handler = logging.FileHandler(log_file, encoding='utf-8')
        
        # Định dạng log (chỉ lưu message vì message của chúng ta sẽ là chuỗi JSON)
        formatter = logging.Formatter('%(message)s')
        file_handler.setFormatter(formatter)
        
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(logging.Formatter('[%(levelname)s] %(message)s'))
        
        # Tránh add nhiều handler nếu init nhiều lần
        if not self.logger.handlers:
            self.logger.addHandler(file_handler)
            self.logger.addHandler(console_handler)

    def log_event(self, event_type: str, data: Dict[str, Any]):
        """Ghi log sự kiện dưới dạng JSON line để dễ parse"""
        payload = {
            "timestamp": datetime.utcnow().isoformat(),
            "event": event_type,
            "data": data
        }
        # Dump json với ensure_ascii=False để đọc được tiếng Việt nếu có
        self.logger.info(json.dumps(payload, ensure_ascii=False))

    def info(self, msg: str):
        self.logger.info(msg)

    def error(self, msg: str, data: Dict[str, Any] = None):
        payload = {
            "timestamp": datetime.utcnow().isoformat(),
            "event": "ERROR",
            "message": msg,
            "data": data
        }
        self.logger.error(json.dumps(payload, ensure_ascii=False))

# Global logger instance
logger = IndustryLogger()