from collections import deque
from datetime import datetime
from typing import List, Dict

_history = deque(maxlen=50)

def record_chat_message(role: str, content: str):
    if not content or not isinstance(content, str):
        return
    clean_content = content.strip()
    if not clean_content:
        return
    _history.append({
        "role": role,
        "content": clean_content,
        "time": datetime.now().strftime("%H:%M:%S")
    })

def get_chat_history() -> List[Dict[str, str]]:
    return list(_history)
