import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    naver_id: str
    naver_secret: str
    youtube_key: str
    gemini_key: str
    gemini_model: str
    google_key: str
    google_cx: str
    openai_key: str
    openai_model: str
    root: Path

    @property
    def naver_ready(self) -> bool:
        return bool(self.naver_id and self.naver_secret)

    @property
    def openai_ready(self) -> bool:
        return bool(self.openai_key)

    @property
    def youtube_ready(self) -> bool:
        return bool(self.youtube_key)

    @property
    def google_ready(self) -> bool:
        return bool(self.google_key and self.google_cx)


def load_env_file(path: Path) -> None:
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def load_settings(root: Path | None = None) -> Settings:
    root = root or Path.cwd()
    load_env_file(root / ".env")
    return Settings(
        naver_id=os.environ.get("NAVER_CLIENT_ID", "").strip(),
        naver_secret=os.environ.get("NAVER_CLIENT_SECRET", "").strip(),
        youtube_key=os.environ.get("YOUTUBE_API_KEY", "").strip(),
        gemini_key=os.environ.get("GEMINI_API_KEY", "").strip(),
        gemini_model=os.environ.get("GEMINI_MODEL", "gemini-3.8-flash").strip() or "gemini-3.8-flash",
        google_key=os.environ.get("GOOGLE_CSE_KEY", "").strip(),
        google_cx=os.environ.get("GOOGLE_CSE_CX", "").strip(),
        openai_key=os.environ.get("OPENAI_API_KEY", "").strip(),
        openai_model=os.environ.get("OPENAI_MODEL", "gpt-6.1-sol").strip() or "gpt-6.1-sol",
        root=root,
    )


def missing_key_names(settings: Settings) -> list[str]:
    missing = []
    if not settings.naver_ready:
        missing.append("NAVER_CLIENT_ID / NAVER_CLIENT_SECRET")
    if not settings.youtube_ready:
        missing.append("YOUTUBE_API_KEY")
    if not settings.gemini_key:
        missing.append("GEMINI_API_KEY")
    if not settings.google_ready:
        missing.append("GOOGLE_CSE_KEY / GOOGLE_CSE_CX")
    return missing
