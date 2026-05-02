from pydantic import BaseModel


class SessionView(BaseModel):
    session_id: str
