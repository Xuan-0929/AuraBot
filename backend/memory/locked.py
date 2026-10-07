"""Build the fixed, read-only benchmark Memory inside an Electron-owned session directory.

The bundled synthetic fixture has stable IDs, timestamps and order. Initialization writes only the
temporary SQLite/Qdrant stores, then opens SQLite read-only and returns the normal MemoryService.
Retrieval, embedding and shutdown keep their production behavior; no extractor or model is called.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import ExitStack, closing
from pathlib import Path

from ..launch_options import MEMORY_FIXTURE
from .embedding import HashEmbeddingProvider
from .service import MemoryService
from .stores.qdrant import QdrantLocalVectorStore
from .stores.sqlite import SQLiteMemoryStore


def build_locked_memory(directory: Path, profile_id: str) -> MemoryService:
    fixture = json.loads((Path(__file__).parent / 'fixtures' / f'{MEMORY_FIXTURE}.json').read_text(encoding='utf-8'))
    if fixture['version'] != MEMORY_FIXTURE or not fixture['memories']:
        raise ValueError('invalid_memory_fixture')
    database = directory / 'memory.sqlite3'
    if not database.exists():
        # Schema creation and fixture insertion finish before exposing this database to retrieval.
        schema = SQLiteMemoryStore(database)
        schema.close()
        try:
            _seed(database, profile_id, fixture)
        except BaseException:
            database.unlink(missing_ok=True)
            raise
    embedding = HashEmbeddingProvider()
    with ExitStack() as cleanup:
        metadata = SQLiteMemoryStore(database, read_only=True)
        cleanup.callback(metadata.close)
        episode = metadata.get_episode_by_source(profile_id=profile_id, source_type='benchmark_fixture', source_id=MEMORY_FIXTURE)
        if episode is None:
            raise ValueError('invalid_memory_fixture_database')
        vectors = QdrantLocalVectorStore(path=directory / 'qdrant', vector_size=embedding.dimension)
        cleanup.callback(vectors.close)
        # Rebuild the small derived index deterministically, also repairing interrupted initialization.
        for memory in metadata.list_active_memories(profile_id):
            vectors.upsert(memory['memory_id'], embedding.embed(memory['content']), {
                'memory_id':memory['memory_id'], 'profile_id':profile_id, 'state':'active',
                'memory_type':memory['memory_type'], 'importance':memory['importance'],
            })
        service = MemoryService(metadata_store=metadata, vector_store=vectors,
            embedding_provider=embedding, read_only=True)
        cleanup.pop_all()
        return service


def _seed(path: Path, profile_id: str, fixture: dict) -> None:
    """Insert trusted bundled data with bound Profile parameters; no user data is imported."""
    timestamp = fixture['timestamp']
    identifier = lambda value: f'{MEMORY_FIXTURE}:{value}'
    with closing(sqlite3.connect(path)) as database, database:
        database.execute('''INSERT INTO memory_episodes
            (id,profile_id,source_type,source_id,content,summary,created_at)
            VALUES (?,?,?,?,?,?,?)''', (MEMORY_FIXTURE,profile_id,'benchmark_fixture',MEMORY_FIXTURE,
            'Synthetic benchmark memory', 'Fixed synthetic preferences and project facts',timestamp))
        entities = sorted({entity for memory in fixture['memories'] for entity in memory['entities']})
        for entity in entities:
            database.execute('''INSERT INTO memory_entities
                (id,profile_id,name,entity_type,aliases_json,summary,state,created_at,updated_at)
                VALUES (?,?,?,'concept','[]','','active',?,?)''',
                (identifier('entity:'+entity),profile_id,entity,timestamp,timestamp))
        for memory in fixture['memories']:
            memory_id = identifier(memory['id'])
            database.execute('''INSERT INTO memory_items
                (id,profile_id,episode_id,memory_type,state,content,importance,confidence,metadata_json,created_at,updated_at)
                VALUES (?,?,?,?,'active',?,0.7,0.9,?,?,?)''',
                (memory_id,profile_id,MEMORY_FIXTURE,memory['type'],memory['content'],
                json.dumps({'entities':memory['entities']},ensure_ascii=False),timestamp,timestamp))
            database.execute('INSERT INTO memory_items_fts(memory_id,content) VALUES (?,?)',(memory_id,memory['content']))
        for relation in fixture['relations']:
            database.execute('''INSERT INTO memory_relations
                (id,profile_id,source_id,target_id,relation_type,state,weight,confidence,episode_id,created_at,updated_at)
                VALUES (?,?,?,?,'relates_to','active',0.7,0.9,?,?,?)''',
                (identifier(relation['source']+':'+relation['target']),profile_id,
                identifier(relation['source']),identifier(relation['target']),MEMORY_FIXTURE,timestamp,timestamp))
