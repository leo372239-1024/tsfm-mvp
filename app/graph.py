import hashlib
import json
from neo4j import GraphDatabase
from .config import NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD

SOURCE = "https://www.fhwa.dot.gov/publications/research/operations/its/06108/02.cfm"
PREDICATES = {"CONNECTS_TO", "CONTAINS", "PRODUCES", "MEASURES", "DOCUMENTS"}


def driver():
    return GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))


def init_graph():
    with driver() as db:
        db.verify_connectivity()
        db.execute_query("CREATE CONSTRAINT component_id IF NOT EXISTS FOR (n:Component) REQUIRE n.id IS UNIQUE")
        db.execute_query("CREATE CONSTRAINT fact_id IF NOT EXISTS FOR (n:Fact) REQUIRE n.id IS UNIQUE")
        for subject_id, subject_label, predicate, object_id, object_label, excerpt in [
            ("fhwa.loop_wire", "Inductive loop wire", "CONNECTS_TO", "fhwa.lead_in_wire", "Lead-in wire", "loop wire, and then lead-in wire"),
            ("fhwa.lead_in_wire", "Lead-in wire", "CONNECTS_TO", "fhwa.pull_box", "Pull box", "lead-in cable in the pull box"),
            ("fhwa.pull_box", "Pull box", "CONNECTS_TO", "fhwa.lead_in_cable", "Lead-in cable", "lead-in cable in the pull box"),
            ("fhwa.lead_in_cable", "Lead-in cable", "CONNECTS_TO", "fhwa.electronics_unit", "Electronics unit", "enters the electronics unit in the controller cabinet"),
            ("fhwa.controller_cabinet", "Controller cabinet", "CONTAINS", "fhwa.electronics_unit", "Electronics unit", "electronics unit in the controller cabinet"),
            ("fhwa.electronics_unit", "Electronics unit", "PRODUCES", "fhwa.presence_pulse", "Presence or pulse output", "produces a presence or pulse output"),
        ]:
            add_fact(dict(subject_id=subject_id, subject_label=subject_label,
                          predicate=predicate, object_id=object_id, object_label=object_label,
                          source_uri=SOURCE, source_anchor="Figure 2-1 (notional)",
                          source_excerpt=excerpt, scope="reference"), db=db)


def add_fact(fact: dict, db=None):
    if fact["predicate"] not in PREDICATES:
        raise ValueError("unsupported predicate")
    canonical = json.dumps(fact, ensure_ascii=False, sort_keys=True)
    fact_id = hashlib.sha256(canonical.encode()).hexdigest()
    own = db is None
    if own:
        db = driver()
    try:
        query = f"""
        MERGE (s:Component {{id:$subject_id}})
          ON CREATE SET s.label=$subject_label
        MERGE (o:Component {{id:$object_id}})
          ON CREATE SET o.label=$object_label
        WITH s,o
        WHERE s.label=$subject_label AND o.label=$object_label
        MERGE (f:Fact {{id:$fact_id}})
          ON CREATE SET f.predicate=$predicate, f.source_uri=$source_uri,
                        f.source_anchor=$source_anchor, f.source_excerpt=$source_excerpt,
                        f.scope=$scope, f.created_at=datetime()
        MERGE (f)-[:SUBJECT]->(s)
        MERGE (f)-[:OBJECT]->(o)
        MERGE (s)-[:{fact['predicate']} {{fact_id:$fact_id}}]->(o)
        RETURN f.id AS id
        """
        records, _, _ = db.execute_query(query, fact_id=fact_id, **fact)
        if not records:
            raise ValueError("component id already has a different label")
        return records[0]["id"]
    finally:
        if own:
            db.close()


def facts(scope=None):
    with driver() as db:
        records, _, _ = db.execute_query("""
        MATCH (f:Fact)-[:SUBJECT]->(s:Component)
        MATCH (f)-[:OBJECT]->(o:Component)
        WHERE $scope IS NULL OR f.scope=$scope
        RETURN f.id AS id, s.id AS subject_id, s.label AS subject_label,
               f.predicate AS predicate, o.id AS object_id, o.label AS object_label,
               f.source_uri AS source_uri, f.source_anchor AS source_anchor,
               f.source_excerpt AS source_excerpt, f.scope AS scope
        ORDER BY subject_id, predicate, object_id
        """, scope=scope)
        return [dict(r) for r in records]

