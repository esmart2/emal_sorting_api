from pydantic import BaseModel
from datetime import datetime
from typing import Optional

class Email(BaseModel):
    id: str
    subject: str
    sender: str
    recipient: str
    content: str
    received_at: datetime
    category: Optional[str] = None
    created_at: datetime
    updated_at: datetime 