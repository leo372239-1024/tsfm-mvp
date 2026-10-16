import os

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://tsfm:tsfm@localhost:5432/tsfm")
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "")
API_KEY = os.getenv("APP_API_KEY", "")
TIMECHO_API_KEY = os.getenv("TIMECHO_API_KEY", "")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
MODEL_PATH = os.getenv("MODEL_PATH", "artifacts/traffic_model.joblib")
DATA_PATH = os.getenv("DATA_PATH", "data/metro_hourly.csv")
ALERT_COOLDOWN_HOURS = float(os.getenv("ALERT_COOLDOWN_HOURS", "3"))
ALERT_REARM_SCORE = float(os.getenv("ALERT_REARM_SCORE", "3"))

