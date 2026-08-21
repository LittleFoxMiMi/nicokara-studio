from typing import Any
from pydantic import BaseModel, Field
class ProjectCreate(BaseModel): name: str = Field(min_length=1,max_length=120)
class ProjectPatch(BaseModel): name: str|None = Field(default=None,min_length=1,max_length=120); title: str|None=None; artist: str|None=None
class DocumentSave(BaseModel): revision: int = Field(ge=1); document: dict[str,Any]
class SettingsPayload(BaseModel): values: dict[str,Any]
class ProjectResponse(BaseModel): id: str; name: str; title: str; artist: str; status: str; created_at: str; updated_at: str; revision: int; deleted_at: str|None=None
