import json
from dataclasses import dataclass, field
from typing import List


@dataclass
class Config:
    token: str
    owner_id: int
    accountant_id: int
    worker_ids: List[int]
    usd_to_uzs: float
    timezone: str
    miniapp_url: str = ""

    def get_role(self, user_id: int) -> str:
        if user_id == self.owner_id:
            return "owner"
        if user_id == self.accountant_id:
            return "accountant"
        if user_id in self.worker_ids:
            return "manager"
        return "unknown"

    def is_authorized(self, user_id: int) -> bool:
        return self.get_role(user_id) != "unknown"

    def all_staff_ids(self) -> List[int]:
        ids = list(self.worker_ids)
        if self.accountant_id:
            ids.append(self.accountant_id)
        return ids


def load_config(path: str = "config.json") -> Config:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return Config(
        token=data["token"],
        owner_id=int(data["owner_id"]),
        accountant_id=int(data["accountant_id"]),
        worker_ids=[int(x) for x in data.get("worker_ids", [])],
        usd_to_uzs=float(data.get("usd_to_uzs", 12800)),
        timezone=data.get("timezone", "Asia/Tashkent"),
        miniapp_url=data.get("miniapp_url", ""),
    )
