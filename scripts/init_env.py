"""Create local deployment secrets without printing them."""
from pathlib import Path
import secrets

path = Path(__file__).resolve().parents[1] / ".env"
if path.exists():
    raise SystemExit(".env already exists; left unchanged")
values = {name: secrets.token_urlsafe(36) for name in
          ("APP_API_KEY", "POSTGRES_PASSWORD", "NEO4J_PASSWORD")}
values.update({"TIMECHO_API_KEY": "", "OPENROUTER_API_KEY": ""})
path.write_text("".join(f"{key}={value}\n" for key, value in values.items()), encoding="utf-8")
print("Created .env with local random secrets. Add optional provider keys manually if needed.")

