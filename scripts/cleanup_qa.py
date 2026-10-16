"""Remove only synthetic QA-* assets created by the bundled acceptance scripts."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.db import connection, init_pool, close_pool
from app.graph import driver


def main():
    init_pool()
    try:
        with connection() as conn:
            asset_ids = [r["id"] for r in conn.execute("SELECT id FROM assets WHERE id LIKE 'QA-%'").fetchall()]
            if asset_ids:
                conn.execute("DELETE FROM reports WHERE asset_id = ANY(%s)", (asset_ids,))
                conn.execute("DELETE FROM alerts WHERE asset_id = ANY(%s)", (asset_ids,))
                conn.execute("DELETE FROM detector_state WHERE asset_id = ANY(%s)", (asset_ids,))
                conn.execute("DELETE FROM telemetry WHERE asset_id = ANY(%s)", (asset_ids,))
                conn.execute("DELETE FROM assets WHERE id = ANY(%s)", (asset_ids,))
        with driver() as graph:
            graph.execute_query("""MATCH (f:Fact)-[:SUBJECT]->(s:Component)
                                   WHERE s.id STARTS WITH 'QA-' DETACH DELETE f""")
            graph.execute_query("MATCH (n:Component) WHERE n.id STARTS WITH 'QA-' DETACH DELETE n")
        print(f"Removed {len(asset_ids)} synthetic QA assets")
    finally:
        close_pool()


if __name__ == "__main__":
    main()

